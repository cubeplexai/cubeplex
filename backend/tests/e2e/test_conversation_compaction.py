"""E2E: compaction triggers, summary persists, full history still surfaces.

These tests use the in-process MemorySaver from the memory_client setup so we
can both drive the API and inspect raw checkpointer state. The CompactionMiddleware
is enabled via env vars (read by config at agent build time) with a deliberately
tiny threshold so we don't have to push thousands of real tokens through.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass
from typing import Any

import httpx
import pytest
import pytest_asyncio
from langchain_core.runnables import RunnableConfig

from tests.e2e.conftest import (
    DEFAULT_TEST_EMAIL,
    DEFAULT_TEST_PASSWORD,
    DEFAULT_WS_ID,
    _ensure_default_user_and_membership,
    _lifespan_context,
    _login_and_attach,
    _make_memory_test_app,
    collect_sse_events,
)

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def compaction_client() -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    """memory_client + memory_saver handle, with compaction forced on at low threshold.

    Yields (client, memory_saver). The saver lets tests inspect raw thread state
    (state.compaction, state.compaction_until_msg_index) which the public API
    does not surface.

    Uses config.set() to override values directly because dynaconf caches at
    import — monkeypatch.setenv doesn't propagate after the singleton has loaded.
    """
    from cubebox.config import config as _cfg

    overrides: dict[str, Any] = {
        "compaction.enabled": True,
        # threshold_ratio * fallback_context_window = 0.001 * 64000 = 64 tokens — trips fast
        "compaction.threshold_ratio": 0.001,
        "compaction.keep_recent_messages": 2,
        "compaction.min_compact_messages": 2,
        # Use the same E2E LLM for summarization (cheap to set, avoids dep on a
        # specific provider's existence in test config).
        "compaction.summary_provider": _cfg.get("llm.default_model", "cubebox/x").split("/")[0],
        "compaction.summary_model": _cfg.get("llm.default_model", "cubebox/x").split("/", 1)[-1],
    }
    saved: dict[str, Any] = {k: _cfg.get(k) for k in overrides}
    for k, v in overrides.items():
        _cfg.set(k, v)

    try:
        await _ensure_default_user_and_membership()
        app = _make_memory_test_app()
        app.state.deployment_mode = "multi_tenant"
        async with _lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                await _login_and_attach(c, DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD)
                yield c, app.state.memory_saver
    finally:
        for k, v in saved.items():
            _cfg.set(k, v)


async def _create_conversation(client: httpx.AsyncClient) -> str:
    r = await client.post(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "compact"})
    r.raise_for_status()
    return str(r.json()["id"])


async def _send(client: httpx.AsyncClient, cid: str, text: str) -> list[dict[str, Any]]:
    return await collect_sse_events(
        client,
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{cid}/messages",
        json_data={"content": text},
    )


def _read_state(memory_saver: Any, cid: str) -> dict[str, Any]:
    """Pull the latest checkpoint values for a thread; normalize compaction to dict."""
    cfg = RunnableConfig(configurable={"thread_id": cid})
    snap = memory_saver.get(cfg)
    if not snap:
        return {}
    values: dict[str, Any] = dict(snap.get("channel_values") or {})
    comp = values.get("compaction")
    if is_dataclass(comp) and not isinstance(comp, type):
        values["compaction"] = asdict(comp)
    return values


@pytest.mark.asyncio
async def test_long_conversation_triggers_compaction(
    compaction_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """Send enough turns to push past the (tiny) threshold; verify compaction lands
    and the messages API still returns the complete original history."""
    client, saver = compaction_client
    cid = await _create_conversation(client)

    for i in range(4):
        await _send(client, cid, f"turn {i}: tell me a one-sentence fact, no preamble")

    state = _read_state(saver, cid)
    assert state.get("compaction") is not None, "expected compaction state to be populated"
    assert state["compaction"]["summary"], "summary text should be non-empty"
    assert state.get("compaction_until_msg_index", 0) > 0

    r = await client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{cid}/messages")
    r.raise_for_status()
    msgs = r.json()["messages"]
    user_count = sum(1 for m in msgs if m["role"] == "user")
    assert user_count == 4, f"UI history must keep all 4 user turns, got {user_count}"


@pytest.mark.asyncio
async def test_summary_stable_when_boundary_unchanged(
    compaction_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """If a follow-up turn doesn't push the boundary forward, the summary text
    must not change — guards the "no needless re-compaction" invariant.
    """
    client, saver = compaction_client
    cid = await _create_conversation(client)

    for i in range(4):
        await _send(client, cid, f"turn {i}: one-line fact, no preamble")

    s1 = _read_state(saver, cid)
    assert s1.get("compaction"), "expected compaction state populated after 4 turns"
    summary_v1 = s1["compaction"]["summary"]
    until_v1 = s1["compaction_until_msg_index"]

    await _send(client, cid, "tiny follow-up")

    s2 = _read_state(saver, cid)
    summary_v2 = s2["compaction"]["summary"]
    until_v2 = s2["compaction_until_msg_index"]

    if until_v2 == until_v1:
        assert summary_v2 == summary_v1, "summary changed without boundary moving"


@pytest.mark.asyncio
async def test_compaction_keeps_tool_pairs_intact(
    compaction_client: tuple[httpx.AsyncClient, Any],
) -> None:
    """When the LLM uses a tool during the conversation and compaction triggers,
    the kept window must never leave an orphan ToolMessage (one whose parent
    AIMessage with that tool_call_id was folded into the summary).
    """
    from langchain_core.messages import AIMessage, ToolMessage

    client, saver = compaction_client
    cid = await _create_conversation(client)

    # Encourage at least one tool call: ask twice for the current date via the
    # datetime tool, plus filler turns to push past the threshold.
    await _send(client, cid, "use the datetime tool to tell me today's date")
    await _send(client, cid, "thanks. now use the datetime tool again with include_time")
    await _send(client, cid, "give me one short fact, no preamble")
    await _send(client, cid, "another short fact, no preamble")

    state = _read_state(saver, cid)
    if not state.get("compaction"):
        pytest.skip("compaction did not trigger in this run; threshold not crossed")
    boundary = state["compaction_until_msg_index"]
    assert boundary > 0

    msgs = state["messages"]
    suffix = msgs[boundary:]

    # Build the set of tool_call_ids declared by AIMessages in the suffix.
    declared: set[str] = set()
    for m in suffix:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    declared.add(tc_id)

    # Every ToolMessage in the suffix must have its parent AIMessage in the suffix.
    orphans = [
        m
        for m in suffix
        if isinstance(m, ToolMessage) and m.tool_call_id and m.tool_call_id not in declared
    ]
    assert not orphans, f"compaction split tool_call/tool_result pair: {orphans}"
