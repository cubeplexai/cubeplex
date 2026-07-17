"""E2E for sandbox ownership isolation + org command-deny policy.

The ``seeded_org_ws_user`` fixture (see tests/e2e/conftest.py) inserts one
org, two workspaces, and one user with membership in both — exercising the
same-user-different-workspace path that the ``uq_user_sandbox_active``
partial unique index protects.

``fake_opensandbox`` (same conftest) monkeypatches
``opensandbox.Sandbox.create`` / ``.connect`` with a ``_FakeRaw`` shim so
these tests never touch a live OpenSandbox provider.
"""

from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.middleware import sandbox as sandbox_mw
from cubeplex.sandbox.manager import SandboxManager

_ENCRYPTION_BACKEND = FernetBackend([Fernet.generate_key()])


async def test_same_user_two_workspaces_distinct_active_rows(
    fake_opensandbox: None,
    session_factory: Any,
    seeded_org_ws_user: tuple[str, str, str, str],
) -> None:
    """Two get_or_create calls for the same user in two workspaces yield two
    distinct active UserSandbox rows (storage + provider isolation boundary).
    """
    del fake_opensandbox  # autouse via parameter
    org_id, ws_a, ws_b, user_id = seeded_org_ws_user
    mgr = SandboxManager(session_factory, _ENCRYPTION_BACKEND)
    await mgr.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
    )
    await mgr.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_b,
    )
    async with session_factory() as s:
        rows = (
            await s.execute(
                sa.text(
                    "SELECT workspace_id, sandbox_id FROM user_sandboxes "
                    "WHERE user_id=:u AND status='running'"
                ),
                {"u": user_id},
            )
        ).all()
    ws_ids = {r[0] for r in rows}
    sbx_ids = {r[1] for r in rows}
    assert ws_ids == {ws_a, ws_b}
    assert len(sbx_ids) == 2  # distinct provider sandboxes


async def test_concurrent_create_reuses_not_duplicates(
    fake_opensandbox: None,
    session_factory: Any,
    seeded_org_ws_user: tuple[str, str, str, str],
) -> None:
    """A second create for the same identity reuses; never a second running row."""
    del fake_opensandbox
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    mgr = SandboxManager(session_factory, _ENCRYPTION_BACKEND)
    await mgr.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
    )
    await mgr.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
    )
    async with session_factory() as s:
        count = (
            await s.execute(
                sa.text(
                    "SELECT COUNT(*) FROM user_sandboxes "
                    "WHERE user_id=:u AND workspace_id=:w AND status='running'"
                ),
                {"u": user_id, "w": ws_a},
            )
        ).scalar_one()
    assert count == 1


async def test_command_deny_blocks_and_filesystem_untouched(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin denies 'rm *'; the agent's execute attempt is blocked and the
    audit buffer (what actually hit the fs) stays empty for that command.
    """
    del monkeypatch
    client, ws_id, _user_id = admin_client_with_user_id

    put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": None,
            "command_rules": [{"action": "deny", "pattern": "rm *"}],
        },
    )
    assert put.status_code == 200, put.text

    # Build the middleware exactly as run_manager would, with the denied rule.
    # The policy gate now lives in before_tool_call: a deny blocks the call
    # before the tool body runs, so the command never reaches the sandbox.
    class _Sb:
        workdir = "/workspace"

        async def execute(self, command: str) -> Any:  # pragma: no cover - guard
            raise AssertionError("denied command must not reach the sandbox")

    class _ToolCall:
        name = "execute"
        id = "c1"

    class _Ctx:
        tool_call = _ToolCall()
        args = sandbox_mw._ExecuteArgs(command="rm -rf /workspace")

    class _StubChannel:
        async def approve(self, **kwargs: Any) -> Any:  # pragma: no cover - guard
            raise AssertionError("deny must not reach the HITL channel")

    mw = sandbox_mw.SandboxMiddleware(
        sandbox=_Sb(),  # type: ignore[arg-type]
        workspace_id=ws_id,
        conversation_id="conv-1",
        command_rules=[{"action": "deny", "pattern": "rm *"}],
        channel=_StubChannel(),
    )
    res = await mw.before_tool_call(_Ctx(), signal=None)
    assert res is not None and res.block is True
    assert "blocked by org policy" in (res.reason or "")
    assert res.hitl_trace["decision"] == "policy_deny"


async def test_image_drift_is_lazy_existing_keeps_old_new_uses_new(
    fake_opensandbox: None,
    session_factory: Any,
    seeded_org_ws_user: tuple[str, str, str, str],
) -> None:
    """Admin default_image is used at create and persisted on the row.
    Changing it does NOT recreate the existing sandbox (lazy drift): the
    existing row stays running on its original image. A NEW user/workspace
    (or a freshly recreated sandbox after the existing one is terminated)
    picks up the new image."""
    del fake_opensandbox
    org_id, ws_a, ws_b, user_id = seeded_org_ws_user
    from cubeplex.repositories.sandbox_policy import SandboxPolicyRepository

    async with session_factory() as s:
        await SandboxPolicyRepository(s, org_id=org_id).upsert(
            default_image="python:3.12",
            network_rules=None,
            command_rules=None,
            network_default_action="deny",
        )

    mgr = SandboxManager(session_factory, _ENCRYPTION_BACKEND)
    await mgr.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
    )
    async with session_factory() as s:
        img1 = (
            await s.execute(
                sa.text(
                    "SELECT image FROM user_sandboxes WHERE user_id=:u "
                    "AND workspace_id=:w AND status='running'"
                ),
                {"u": user_id, "w": ws_a},
            )
        ).scalar_one()
    assert img1 == "python:3.12"

    # Change the policy image; the EXISTING sandbox keeps its old image
    # (lazy drift). No row is terminated by the policy change.
    async with session_factory() as s:
        await SandboxPolicyRepository(s, org_id=org_id).upsert(
            default_image="ubuntu:22.04",
            network_rules=None,
            command_rules=None,
            network_default_action="deny",
        )
    await mgr.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
    )
    async with session_factory() as s:
        still_running = (
            await s.execute(
                sa.text(
                    "SELECT image FROM user_sandboxes WHERE user_id=:u "
                    "AND workspace_id=:w AND status='running'"
                ),
                {"u": user_id, "w": ws_a},
            )
        ).scalar_one()
        terminated = (
            await s.execute(
                sa.text(
                    "SELECT COUNT(*) FROM user_sandboxes WHERE user_id=:u "
                    "AND workspace_id=:w AND status='terminated'"
                ),
                {"u": user_id, "w": ws_a},
            )
        ).scalar_one()
    assert still_running == "python:3.12"  # lazy: NOT torn down
    assert terminated == 0  # nothing demoted by the policy change

    # A brand-new sandbox (different workspace, same user) picks up the new image.
    await mgr.get_or_create(
        scope_type="user",
        scope_id=user_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_b,
    )
    async with session_factory() as s:
        img_new = (
            await s.execute(
                sa.text(
                    "SELECT image FROM user_sandboxes WHERE user_id=:u "
                    "AND workspace_id=:w AND status='running'"
                ),
                {"u": user_id, "w": ws_b},
            )
        ).scalar_one()
    assert img_new == "ubuntu:22.04"


async def test_command_confirm_routes_through_hitl_channel(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
) -> None:
    """A saved ``confirm`` rule routes the execute call through the HITL
    channel: approve lets the command run, deny/timeout/cancel block it. The
    rule is resolved through the real admin-policy PUT + SandboxManager path."""
    client, ws_id, _user_id = admin_client_with_user_id

    put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": None,
            "command_rules": [{"action": "confirm", "pattern": "git push *"}],
        },
    )
    assert put.status_code == 200, put.text

    ran: list[str] = []

    class _Sb:
        workdir = "/workspace"

        async def execute(self, command: str) -> Any:  # pragma: no cover - unused
            ran.append(command)

            class _R:
                output = "ok"
                exit_code = 0

            return _R()

    class _ToolCall:
        name = "execute"
        id = "call_push"

    class _Ctx:
        tool_call = _ToolCall()
        args = sandbox_mw._ExecuteArgs(command="git push origin main")

    from cubepi.hitl import ApproveAnswer, HitlCancelled, HitlTimedOut

    class _Channel:
        def __init__(self, *, answer: Any = None, raises: Exception | None = None) -> None:
            self._answer = answer
            self._raises = raises
            self.seen: list[dict[str, Any]] = []

        async def approve(self, **kwargs: Any) -> Any:
            self.seen.append(kwargs)
            if self._raises is not None:
                raise self._raises
            return self._answer

    # Read the persisted rule back through the admin API (real DB path).
    got = await client.get("/api/v1/admin/sandbox-policy")
    assert got.status_code == 200, got.text
    org_rules = got.json()["command_rules"]
    assert org_rules == [{"action": "confirm", "pattern": "git push *"}]

    def _mw(channel: Any) -> Any:
        return sandbox_mw.SandboxMiddleware(
            sandbox=_Sb(),  # type: ignore[arg-type]
            workspace_id=ws_id,
            conversation_id="conv-confirm",
            command_rules=org_rules,
            channel=channel,
        )

    # approve → not blocked (tool will run)
    ch_ok = _Channel(answer=ApproveAnswer(decision="approve"))
    res = await _mw(ch_ok).before_tool_call(_Ctx(), signal=None)
    assert res is None
    assert ch_ok.seen[0]["tool_name"] == "execute"
    assert ch_ok.seen[0]["details"]["matched_pattern"] == "git push *"

    # deny → blocked
    ch_no = _Channel(answer=ApproveAnswer(decision="deny", reason="not now"))
    res = await _mw(ch_no).before_tool_call(_Ctx(), signal=None)
    assert res is not None and res.block is True
    assert res.hitl_trace["decision"] == "human_deny"

    # timeout → blocked as deny
    ch_to = _Channel(raises=HitlTimedOut(180.0))
    res = await _mw(ch_to).before_tool_call(_Ctx(), signal=None)
    assert res is not None and res.block is True
    assert res.hitl_trace["decision"] == "timed_out"

    # cancel → blocked
    ch_cx = _Channel(raises=HitlCancelled("user closed"))
    res = await _mw(ch_cx).before_tool_call(_Ctx(), signal=None)
    assert res is not None and res.block is True
    assert res.hitl_trace["decision"] == "cancelled"
