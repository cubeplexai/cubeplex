"""Unit tests for SkillsMiddleware (M3.c.2).

Covers:
- after_tool_call ignores non-load_skill tools.
- after_tool_call on successful load_skill writes to extra["loaded_skills"].
- after_tool_call on errored load_skill does nothing.
- after_tool_call on load_skill with loaded=False does nothing.
- transform_system_prompt is pass-through when no loaded skills.
- transform_system_prompt appends skills sorted by name (deterministic/cache-stable).
- Round-trip: after_tool_call writes; transform_system_prompt reads via same extra dict.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from cubepi.agent.types import AfterToolCallContext, AgentContext, AgentToolResult
from cubepi.providers.base import AssistantMessage, TextContent, ToolCall

from cubebox.middleware.skills import SkillsMiddleware
from cubebox.tools.builtin.load_skill import LoadSkillOutput

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_extra(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def _make_middleware(extra: dict[str, Any]) -> SkillsMiddleware:
    """Build a SkillsMiddleware with a closure over the given extra dict."""
    return SkillsMiddleware(extra_ref=lambda: extra)


def _make_tool_call(name: str = "load_skill", tool_id: str = "tc-1") -> ToolCall:
    return ToolCall(id=tool_id, name=name, arguments={})


def _make_assistant_msg() -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text="ok")], stop_reason="tool_use")


def _make_context(extra: dict[str, Any] | None = None) -> AgentContext:
    return AgentContext(
        system_prompt="base",
        messages=[],
        extra=extra if extra is not None else {},
    )


def _skill_result(
    skill_name: str,
    content: str,
    version: str = "1.0",
    loaded: bool = True,
    error: str | None = None,
) -> AgentToolResult:
    """Build an AgentToolResult carrying a JSON LoadSkillOutput."""
    output = LoadSkillOutput(
        skill_name=skill_name,
        content=content,
        version=version,
        loaded=loaded,
        error=error,
    )
    return AgentToolResult(content=[TextContent(text=output.model_dump_json())])


def _make_after_ctx(
    tool_name: str = "load_skill",
    result: AgentToolResult | None = None,
    is_error: bool = False,
    extra: dict[str, Any] | None = None,
) -> AfterToolCallContext:
    """Build a minimal AfterToolCallContext for testing."""
    if result is None:
        result = _skill_result("my-skill", "# Skill content")
    agent_ctx = _make_context(extra)
    return AfterToolCallContext(
        assistant_message=_make_assistant_msg(),
        tool_call=_make_tool_call(tool_name),
        args=MagicMock(),
        result=result,
        is_error=is_error,
        context=agent_ctx,
    )


# ---------------------------------------------------------------------------
# after_tool_call — non-load_skill tools are ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_tool_call_ignores_other_tools() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)

    ctx = _make_after_ctx(tool_name="execute", result=AgentToolResult(content=[]))
    result = await mw.after_tool_call(ctx)

    assert result is None
    assert _LOADED_KEY not in extra, "should not touch extra for non-load_skill tools"


# ---------------------------------------------------------------------------
# after_tool_call — successful load_skill writes to extra
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_tool_call_writes_skill_to_extra() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)

    ctx = _make_after_ctx(
        result=_skill_result("deep-research", "# Deep Research\n\nDo stuff"),
    )
    result = await mw.after_tool_call(ctx)

    assert result is None  # hook never replaces the tool result
    assert extra.get("loaded_skills") == {"deep-research": "# Deep Research\n\nDo stuff"}


@pytest.mark.asyncio
async def test_after_tool_call_persists_sandbox_path_in_content() -> None:
    # The sandbox `path` must survive into the persisted content so it is
    # re-injected on later turns (the tool result alone is gone by then), or
    # colon-named multi-file skills resume guessing the path.
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)

    result_obj = LoadSkillOutput(
        skill_name="acme:designer",
        content="# Designer\n\nDesign boldly.",
        version="2.0.0",
        loaded=True,
        path="/.skills/acme__designer/2.0.0",
    )
    ctx = _make_after_ctx(
        result=AgentToolResult(content=[TextContent(text=result_obj.model_dump_json())]),
    )
    await mw.after_tool_call(ctx)

    stored = extra["loaded_skills"]["acme:designer"]
    assert "/.skills/acme__designer/2.0.0" in stored
    assert stored.endswith("# Designer\n\nDesign boldly.")

    # And it renders into the system prompt on a subsequent call.
    out = await mw.transform_system_prompt("BASE", ctx=_make_context(extra))
    assert "/.skills/acme__designer/2.0.0" in out


@pytest.mark.asyncio
async def test_after_tool_call_accumulates_multiple_skills() -> None:
    """Loading a second skill appends to existing loaded_skills dict."""
    extra: dict[str, Any] = {"loaded_skills": {"alpha": "Alpha content"}}
    mw = _make_middleware(extra)

    ctx = _make_after_ctx(result=_skill_result("beta", "Beta content"))
    await mw.after_tool_call(ctx)

    assert extra["loaded_skills"] == {"alpha": "Alpha content", "beta": "Beta content"}


@pytest.mark.asyncio
async def test_after_tool_call_overwrites_same_skill_name() -> None:
    """Re-loading the same skill overwrites the previous content."""
    extra: dict[str, Any] = {"loaded_skills": {"my-skill": "old content"}}
    mw = _make_middleware(extra)

    ctx = _make_after_ctx(result=_skill_result("my-skill", "new content"))
    await mw.after_tool_call(ctx)

    assert extra["loaded_skills"]["my-skill"] == "new content"


# ---------------------------------------------------------------------------
# after_tool_call — error cases do nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_tool_call_is_error_does_nothing() -> None:
    """is_error=True → extra not modified."""
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)

    ctx = _make_after_ctx(
        result=_skill_result("s", "", loaded=False, error="not found"),
        is_error=True,
    )
    await mw.after_tool_call(ctx)

    assert "loaded_skills" not in extra


@pytest.mark.asyncio
async def test_after_tool_call_loaded_false_does_nothing() -> None:
    """Tool succeeded at the RPC level but skill was not found (loaded=False)."""
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)

    ctx = _make_after_ctx(
        result=_skill_result("missing-skill", "", loaded=False, error="Skill not enabled"),
        is_error=False,
    )
    await mw.after_tool_call(ctx)

    assert "loaded_skills" not in extra


@pytest.mark.asyncio
async def test_after_tool_call_empty_content_does_nothing() -> None:
    """Result with no TextContent blocks → extra not modified."""
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)

    ctx = _make_after_ctx(result=AgentToolResult(content=[]))
    await mw.after_tool_call(ctx)

    assert "loaded_skills" not in extra


@pytest.mark.asyncio
async def test_after_tool_call_malformed_json_does_nothing() -> None:
    """Unparseable text result → extra not modified (silent skip)."""
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)

    ctx = _make_after_ctx(result=AgentToolResult(content=[TextContent(text="not-json")]))
    await mw.after_tool_call(ctx)

    assert "loaded_skills" not in extra


# ---------------------------------------------------------------------------
# transform_system_prompt — no skills → passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_system_prompt_passthrough_when_empty() -> None:
    extra: dict[str, Any] = {}
    mw = _make_middleware(extra)

    result = await mw.transform_system_prompt("Base prompt.", ctx=object())
    assert result == "Base prompt."


@pytest.mark.asyncio
async def test_transform_system_prompt_passthrough_empty_loaded_skills() -> None:
    extra: dict[str, Any] = {"loaded_skills": {}}
    mw = _make_middleware(extra)

    result = await mw.transform_system_prompt("Base prompt.", ctx=object())
    assert result == "Base prompt."


# ---------------------------------------------------------------------------
# transform_system_prompt — appends sorted skills section
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_system_prompt_appends_single_skill() -> None:
    extra: dict[str, Any] = {"loaded_skills": {"deep-research": "# Deep Research content"}}
    mw = _make_middleware(extra)

    result = await mw.transform_system_prompt("Base prompt.", ctx=object())

    assert result.startswith("Base prompt.")
    assert "[Loaded skills]" in result
    assert "## Skill: deep-research" in result
    assert "# Deep Research content" in result


@pytest.mark.asyncio
async def test_transform_system_prompt_sorts_skills_deterministically() -> None:
    """Skills are rendered in sorted name order (cache discipline)."""
    extra: dict[str, Any] = {
        "loaded_skills": {
            "zzz-skill": "ZZZ content",
            "aaa-skill": "AAA content",
            "mmm-skill": "MMM content",
        }
    }
    mw = _make_middleware(extra)

    result = await mw.transform_system_prompt("Base.", ctx=object())

    aaa_pos = result.index("aaa-skill")
    mmm_pos = result.index("mmm-skill")
    zzz_pos = result.index("zzz-skill")
    assert aaa_pos < mmm_pos < zzz_pos, "Skills must appear in sorted (alphabetical) order"


@pytest.mark.asyncio
async def test_transform_system_prompt_deterministic_output() -> None:
    """Same inputs always yield same output (cache stability)."""
    extra: dict[str, Any] = {"loaded_skills": {"beta": "Beta content", "alpha": "Alpha content"}}
    mw = _make_middleware(extra)

    result1 = await mw.transform_system_prompt("Base.", ctx=object())
    result2 = await mw.transform_system_prompt("Base.", ctx=object())
    assert result1 == result2


# ---------------------------------------------------------------------------
# Round-trip: after_tool_call writes, transform_system_prompt reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trip_via_shared_extra_dict() -> None:
    """after_tool_call and transform_system_prompt share the same extra dict."""
    shared_extra: dict[str, Any] = {}
    mw = _make_middleware(shared_extra)

    # Step 1: simulate a successful load_skill tool call
    ctx = _make_after_ctx(
        result=_skill_result("my-skill", "# My Skill\n\nThis is the skill body."),
        extra=shared_extra,
    )
    await mw.after_tool_call(ctx)

    # Verify it was written
    assert shared_extra.get("loaded_skills") == {
        "my-skill": "# My Skill\n\nThis is the skill body."
    }

    # Step 2: system prompt is now augmented
    augmented = await mw.transform_system_prompt("You are a helpful assistant.", ctx=object())

    assert "[Loaded skills]" in augmented
    assert "## Skill: my-skill" in augmented
    assert "# My Skill" in augmented
    assert "This is the skill body." in augmented


@pytest.mark.asyncio
async def test_round_trip_skills_not_visible_before_load() -> None:
    """transform_system_prompt is unchanged until after_tool_call fires."""
    shared_extra: dict[str, Any] = {}
    mw = _make_middleware(shared_extra)

    # Before any load_skill call
    prompt_before = await mw.transform_system_prompt("System.", ctx=object())
    assert prompt_before == "System."

    # After a successful load_skill call
    ctx = _make_after_ctx(result=_skill_result("x-skill", "X content"), extra=shared_extra)
    await mw.after_tool_call(ctx)

    prompt_after = await mw.transform_system_prompt("System.", ctx=object())
    assert "X content" in prompt_after


# ---------------------------------------------------------------------------
# Private constant used in the assertion above
# ---------------------------------------------------------------------------

_LOADED_KEY = "loaded_skills"
