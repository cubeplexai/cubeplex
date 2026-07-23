"""SandboxMiddleware.before_tool_call command-policy gate."""

from __future__ import annotations

import pytest
from cubepi.hitl import ApproveAnswer, HitlCancelled, HitlTimedOut

from cubeplex.middleware.sandbox import SandboxMiddleware


class _ToolCall:
    def __init__(self, name: str, id: str = "call_1") -> None:
        self.name = name
        self.id = id


class _Ctx:
    def __init__(self, name: str, command: str) -> None:
        self.tool_call = _ToolCall(name)
        self.args = {"command": command}


class _StubChannel:
    """Records the approve() call and returns a scripted answer/raises."""

    def __init__(self, *, answer=None, raises: Exception | None = None) -> None:
        self._answer = answer
        self._raises = raises
        self.calls: list[dict] = []

    async def approve(self, *, tool_name, tool_call_id, args, details, timeout=None, signal=None):
        self.calls.append(
            {
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "args": args,
                "details": details,
                "timeout": timeout,
            }
        )
        if self._raises is not None:
            raise self._raises
        return self._answer


class _FakeSandbox:
    workdir = "/work"


def _mw(channel, rules):
    return SandboxMiddleware(
        sandbox=_FakeSandbox(),
        conversation_id="c1",
        workspace_id="w1",
        command_rules=rules,
        channel=channel,
    )


@pytest.mark.asyncio
async def test_non_execute_tool_is_ignored():
    ch = _StubChannel()
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("write_file", "rm -rf /"), signal=None)
    assert res is None
    assert ch.calls == []


@pytest.mark.asyncio
async def test_allow_passes_without_channel_call():
    ch = _StubChannel()
    mw = _mw(ch, [{"action": "deny", "pattern": "shutdown"}])
    res = await mw.before_tool_call(_Ctx("execute", "ls -la"), signal=None)
    assert res is None
    assert ch.calls == []


@pytest.mark.asyncio
async def test_deny_blocks_without_channel_call():
    from cubeplex.services.sandbox_runtime_config import POLICY_DENY_NUDGE

    ch = _StubChannel()
    mw = _mw(ch, [{"action": "deny", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res is not None and res.block is True
    assert ch.calls == []
    assert res.hitl_trace["decision"] == "policy_deny"
    assert POLICY_DENY_NUDGE in (res.reason or "")


@pytest.mark.asyncio
async def test_confirm_approve_runs_tool():
    ch = _StubChannel(answer=ApproveAnswer(decision="approve"))
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res is None  # not blocked -> tool runs
    assert ch.calls[0]["tool_name"] == "execute"
    assert ch.calls[0]["args"] == {"command": "rm -rf /tmp/x"}
    assert ch.calls[0]["details"]["matched_pattern"] == "rm *"
    assert ch.calls[0]["timeout"] is None


@pytest.mark.asyncio
async def test_confirm_deny_blocks():
    ch = _StubChannel(answer=ApproveAnswer(decision="deny", reason="nope"))
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res.block is True
    assert res.hitl_trace["decision"] == "human_deny"
    assert res.hitl_trace["reason"] == "nope"


@pytest.mark.asyncio
async def test_confirm_timeout_blocks_as_deny():
    ch = _StubChannel(raises=HitlTimedOut(180.0))
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res.block is True
    assert res.hitl_trace["decision"] == "timed_out"
    assert res.deny_reason == "approval_timeout"


@pytest.mark.asyncio
async def test_confirm_cancel_blocks():
    ch = _StubChannel(raises=HitlCancelled("user closed"))
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res.block is True
    assert res.hitl_trace["decision"] == "cancelled"


@pytest.mark.asyncio
async def test_confirm_edit_is_rejected():
    ch = _StubChannel(answer=ApproveAnswer(decision="edit", edited_args={"command": "ls"}))
    mw = _mw(ch, [{"action": "confirm", "pattern": "rm *"}])
    with pytest.raises(ValueError):
        await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)


@pytest.mark.asyncio
async def test_no_channel_confirm_rule_fails_closed():
    # confirm rule with no channel must block (fail-closed), not silently allow.
    mw = _mw(None, [{"action": "confirm", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res is not None and res.block is True
    assert res.deny_reason == "hitl_unavailable"


@pytest.mark.asyncio
async def test_no_channel_deny_rule_still_blocks():
    # deny rules must be enforced even when channel is None.
    mw = _mw(None, [{"action": "deny", "pattern": "rm *"}])
    res = await mw.before_tool_call(_Ctx("execute", "rm -rf /tmp/x"), signal=None)
    assert res is not None and res.block is True
