"""Byte-parity test (Tier 3) — M6 gate for default-flag-flip.

For a fixed conversation scenario, runs the request through BOTH runtime
paths and asserts the outbound HTTP request bodies (sent to the LLM
gateway) are byte-identical after canonical-json normalization.

Any divergence is a real cubepi-migration regression: prompt cache may
not hit, telemetry may differ, or content may be drifting silently.

Marked xfail until divergence is resolved — the purpose of the first run
is diagnostic: record the exact delta between langgraph and cubepi.

## M5.4 Diagnostic Results (2026-05-13)

Running test_byte_parity_single_turn revealed 5 structural divergences:

### 1. Missing `max_tokens` in cubepi (CACHE-NEUTRAL, but functional gap)
  LangGraph sends `"max_tokens": 32000`; cubepi OpenAIProvider does not
  forward it. Fix: pass `max_tokens` via `on_payload` or cubepi StreamOptions.

### 2. Missing `temperature` in cubepi (CACHE-NEUTRAL, but functional gap)
  LangGraph sends `"temperature": 0.7`; cubepi does not. Fix: same as above.

### 3. Tools array ordering differs (CACHE-BREAKING)
  LangGraph order (sandbox middleware inserts execute/write_file/edit_file/
  file_read/save_artifact first, then subagent/write_todos, then builtins):
    execute → write_file → edit_file → file_read → save_artifact →
    write_todos → subagent → calculator → datetime → memory_save →
    memory_search → memory_update → load_skill
  Cubepi order (builtin tools first, then middleware tools appended):
    calculator → datetime → view_images → memory_save → memory_search →
    memory_update → load_skill → save_artifact → execute → write_file →
    edit_file → file_read
  Root cause: cubepi all_tools list builds as:
    list_builtin_tools_for_cubepi() + view_images + memory_tools +
    load_skill + mcp_tools + (middleware.tools from ArtifactMiddlewarePi)
  LangGraph builds tools via ToolRegistry with a different registration order.
  Fix: need deterministic sorted tool order in both paths, OR sort by name
  before sending.

### 4. User message content contaminated (CACHE-BREAKING, M5.3 root cause)
  Cubepi's ArtifactMiddlewarePi injects artifact context by APPENDING to
  the user message content:
    content = "Reply with one word.\n\n## Artifacts\n\n...(dynamic text)..."
  LangGraph injects it into the system message (stable prefix).
  This means cubepi's user message changes with conversation state (as
  existing artifact IDs change), which BREAKS the cache stable prefix.
  Fix: ArtifactMiddlewarePi must inject into system_prompt (like langgraph),
  not into user message content. The artifact hint is stable within a
  conversation and belongs in the prefix, not the suffix.

### 5. System message structure differs (CACHE-NEUTRAL after concatenation)
  LangGraph: content = [{type: text, text: base_prompt}, {type: text,
    text: write_todos_prompt}, {type: text, text: subagent_prompt}]
  Cubepi: content = "base_prompt + write_todos_section + subagent_section"
    (a flat concatenated string, but same text content)
  For OpenAI-compatible endpoints both produce equivalent cache keys since
  the provider concatenates before hashing. NOT a cache issue.

### Summary for M5.3 fix
The cache miss on turn 2 under cubepi is caused by item #4:
ArtifactMiddlewarePi appends `## Artifacts\n\n...existing artifacts: None yet.\n`
to the user message. On turn 1: content = "Reply with one word.\n\n## Artifacts\n"
On turn 2: content will change if any artifacts exist. Even on turn 2 with no
artifacts the string "None yet." changes the hash vs turn 1.
Fix ArtifactMiddlewarePi to inject into system_prompt not user message.
Also fix tools ordering (#3) for full parity.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from tests.e2e.conftest import collect_sse_events

pytestmark = pytest.mark.e2e

# Minimal SSE response: one text delta, a stop chunk with usage, then [DONE].
# Enough for both runtimes to complete without error.
_MINIMAL_SSE = (
    'data: {"id":"x","object":"chat.completion.chunk","choices":[{"index":0,'
    '"delta":{"role":"assistant","content":"ok"},"finish_reason":null}]}\n\n'
    'data: {"id":"x","object":"chat.completion.chunk","choices":[{"index":0,'
    '"delta":{},"finish_reason":"stop"}],'
    '"usage":{"prompt_tokens":10,"completion_tokens":1,"total_tokens":11}}\n\n'
    "data: [DONE]\n\n"
)


def _canonical(d: dict) -> str:  # type: ignore[type-arg]
    """Sorted, compact JSON — for stable comparison independent of key order."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def _diff_fields(a: dict, b: dict, path: str = "") -> list[str]:  # type: ignore[type-arg]
    """Recursively collect paths where two dicts differ."""
    diffs: list[str] = []
    all_keys = set(a) | set(b)
    for k in sorted(all_keys):
        full = f"{path}.{k}" if path else str(k)
        if k not in a:
            diffs.append(f"  + {full}: (missing in langgraph, present in cubepi)")
        elif k not in b:
            diffs.append(f"  - {full}: (present in langgraph, missing in cubepi)")
        elif isinstance(a[k], dict) and isinstance(b[k], dict):
            diffs.extend(_diff_fields(a[k], b[k], path=full))
        elif isinstance(a[k], list) and isinstance(b[k], list):
            if a[k] != b[k]:
                if len(a[k]) != len(b[k]):
                    diffs.append(f"  ~ {full}: list length {len(a[k])} (lg) vs {len(b[k])} (pi)")
                else:
                    for i, (ai, bi) in enumerate(zip(a[k], b[k], strict=True)):
                        if ai != bi:
                            if isinstance(ai, dict) and isinstance(bi, dict):
                                diffs.extend(_diff_fields(ai, bi, path=f"{full}[{i}]"))
                            else:
                                diffs.append(f"  ~ {full}[{i}]: {ai!r} vs {bi!r}")
        elif a[k] != b[k]:
            diffs.append(f"  ~ {full}: {a[k]!r} (lg) vs {b[k]!r} (pi)")
    return diffs


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason=(
        "cubepi runtime LLM request body diverges from langgraph; "
        "this test captures the delta for the M5.4 diagnostic. "
        "Remove xfail once parity is achieved."
    ),
    strict=False,
)
async def test_byte_parity_single_turn(
    member_client: tuple,  # type: ignore[type-arg]
) -> None:
    """Fixed scenario: send 'Reply with one word.', capture outbound API request
    body for both runtimes via respx, assert canonical-JSON equality.

    Each runtime gets its own fresh conversation so history cannot cross-contaminate.
    The LLM gateway is mocked — both runtimes see the same deterministic response.

    On first run this test is expected to FAIL.  The diff output documents what
    exactly differs between runtimes so we can fix the cubepi path.
    """
    client, ws_id = member_client
    # The member_client fixture yields a plain httpx.AsyncClient using
    # httpx.ASGITransport — the FastAPI app lives on the transport.
    app = client._transport.app  # type: ignore[union-attr]

    captured: dict[str, dict | None] = {"langgraph": None, "cubepi": None}

    for runtime_name in ("langgraph", "cubepi"):
        # Each runtime gets a brand-new conversation — no shared history.
        resp = await client.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": f"byte-parity-{runtime_name}"},
        )
        resp.raise_for_status()
        conv_id = resp.json()["id"]

        # Override runtime on the live app instance.
        app.state.agents_runtime = runtime_name

        try:
            # respx.mock intercepts ALL httpx traffic globally — both
            # ChatOpenAICompatible (langgraph) and OpenAIProvider (cubepi) route
            # through httpx.AsyncClient internally.
            with respx.mock(assert_all_called=False) as router:
                # Match any URL ending in /chat/completions.
                route = router.post(url__regex=r".*/chat/completions.*").mock(
                    return_value=httpx.Response(
                        200,
                        text=_MINIMAL_SSE,
                        headers={"content-type": "text/event-stream"},
                    )
                )

                try:
                    events = await collect_sse_events(
                        client,
                        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
                        json_data={"content": "Reply with one word."},
                    )
                except Exception as exc:
                    # Surface inner error for better diagnostics.
                    error_events = [
                        e for e in (events if "events" in dir() else []) if e.get("type") == "error"
                    ]  # type: ignore[possibly-undefined]  # noqa: E501
                    pytest.fail(
                        f"{runtime_name}: agent run raised {exc!r}. Error events: {error_events}"
                    )

                # Verify that the LLM gateway was actually called.
                if not route.calls:
                    # Check events for error
                    error_events = [e for e in events if e.get("type") == "error"]
                    pytest.fail(
                        f"{runtime_name}: no HTTP call captured by respx. "
                        f"SSE events: {events[:5]!r}\nErrors: {error_events}"
                    )

                raw_body = route.calls.last.request.content
                captured[runtime_name] = json.loads(raw_body.decode("utf-8"))
        finally:
            # Always restore to None so subsequent tests use config default.
            app.state.agents_runtime = None

    lg = captured["langgraph"]
    pi = captured["cubepi"]

    assert lg is not None, "No langgraph request captured"
    assert pi is not None, "No cubepi request captured"

    # Print both bodies for diagnostic (visible with pytest -s).
    print(f"\n=== langgraph request body ===\n{json.dumps(lg, indent=2, sort_keys=True)}")
    print(f"\n=== cubepi request body ===\n{json.dumps(pi, indent=2, sort_keys=True)}")

    diffs = _diff_fields(lg, pi)
    if diffs:
        print("\n=== Field-level diff (langgraph vs cubepi) ===")
        for line in diffs:
            print(line)

    assert _canonical(lg) == _canonical(pi), (
        f"LLM API request body diverges between runtimes.\n\n"
        f"Field-level diff ({len(diffs)} differences):\n"
        + "\n".join(diffs)
        + f"\n\nFull langgraph body:\n{json.dumps(lg, indent=2, sort_keys=True)}"
        + f"\n\nFull cubepi body:\n{json.dumps(pi, indent=2, sort_keys=True)}"
    )


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason=(
        "cubepi runtime multi-turn prefix diverges from langgraph; "
        "this is the M5.3 cache failure diagnostic (cache_read=0 on turn 2). "
        "Remove xfail once parity is achieved."
    ),
    strict=False,
)
async def test_byte_parity_turn1_vs_turn2_cubepi(
    member_client: tuple,  # type: ignore[type-arg]
) -> None:
    """Multi-turn cache discipline diagnostic.

    Sends the same message twice in the SAME conversation under cubepi.
    The stable prefix (system + pinned memory + tools) must be byte-identical
    across turns for OpenAI-compatible auto-caching to hit.

    This is the M5.3 cache failure root cause test.
    """
    client, ws_id = member_client
    app = client._transport.app  # type: ignore[union-attr]

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": "byte-parity-multiturn-cubepi"},
    )
    resp.raise_for_status()
    conv_id = resp.json()["id"]

    app.state.agents_runtime = "cubepi"
    captured_turns: list[dict] = []

    try:
        for turn_idx in range(1, 3):
            with respx.mock(assert_all_called=False) as router:
                route = router.post(url__regex=r".*/chat/completions.*").mock(
                    return_value=httpx.Response(
                        200,
                        text=_MINIMAL_SSE,
                        headers={"content-type": "text/event-stream"},
                    )
                )

                await collect_sse_events(
                    client,
                    f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
                    json_data={"content": "Reply with one word."},
                )

                if not route.calls:
                    pytest.fail(f"Turn {turn_idx}: no HTTP call captured.")

                raw_body = route.calls.last.request.content
                captured_turns.append(json.loads(raw_body.decode("utf-8")))
    finally:
        app.state.agents_runtime = None

    assert len(captured_turns) == 2, f"Expected 2 captured turns, got {len(captured_turns)}"

    turn1 = captured_turns[0]
    turn2 = captured_turns[1]

    print(f"\n=== cubepi turn 1 request body ===\n{json.dumps(turn1, indent=2, sort_keys=True)}")
    print(f"\n=== cubepi turn 2 request body ===\n{json.dumps(turn2, indent=2, sort_keys=True)}")

    # The stable prefix is: system message + all messages except the last.
    # For an auto-caching provider, the system message + tool list + previous messages
    # must be byte-identical.  Check how the system message changes.
    msgs1 = turn1.get("messages", [])
    msgs2 = turn2.get("messages", [])

    sys1 = [m for m in msgs1 if m.get("role") == "system"]
    sys2 = [m for m in msgs2 if m.get("role") == "system"]

    tools1 = turn1.get("tools", [])
    tools2 = turn2.get("tools", [])

    diffs_system = _diff_fields(sys1[0] if sys1 else {}, sys2[0] if sys2 else {})
    diffs_tools = _diff_fields(
        {"tools": tools1} if tools1 else {},
        {"tools": tools2} if tools2 else {},
    )

    print("\n=== System message diff (turn1 vs turn2) ===")
    print(f"Turn 1 system: {json.dumps(sys1[0] if sys1 else {}, indent=2)}")
    print(f"Turn 2 system: {json.dumps(sys2[0] if sys2 else {}, indent=2)}")
    if diffs_system:
        for line in diffs_system:
            print(line)
    else:
        print("  (identical)")

    print("\n=== Tools diff (turn1 vs turn2) ===")
    if diffs_tools:
        for line in diffs_tools:
            print(line)
    else:
        print("  (identical)")

    # Turn 2 should have one more message than turn 1 (the prior assistant response).
    # Everything before the new user message is the stable prefix.
    assert len(msgs2) == len(msgs1) + 2, (
        f"Turn 2 should have 2 more messages than turn 1 "
        f"(assistant reply + new user message). "
        f"Turn 1: {len(msgs1)} messages, turn 2: {len(msgs2)} messages."
    )

    # The stable prefix = all messages in turn2 except the last one.
    stable_prefix_1 = msgs1[:-1]  # turn 1: all except last (the user message)
    stable_prefix_2 = msgs2[:-1]  # turn 2: all except last (the user message)

    # The stable prefix of turn 2 must contain all of turn 1's prefix verbatim.
    # Any system prompt divergence here = cache miss.
    diffs_prefix = []
    for i, (m1, m2) in enumerate(zip(stable_prefix_1, stable_prefix_2, strict=False)):
        if m1 != m2:
            diffs_prefix.extend(_diff_fields(m1, m2, path=f"messages[{i}]"))

    if diffs_prefix:
        print("\n=== Stable prefix divergence (turn1 vs turn2 shared prefix) ===")
        for line in diffs_prefix:
            print(line)

    assert _canonical(sys1[0] if sys1 else {}) == _canonical(sys2[0] if sys2 else {}), (
        "System message changed between turn 1 and turn 2 — this breaks the "
        "stable prefix invariant required for prompt cache hits.\n"
        "Diff:\n" + "\n".join(diffs_system)
    )

    assert _canonical({"tools": tools1}) == _canonical({"tools": tools2}), (
        "Tools list changed between turn 1 and turn 2 — this breaks the "
        "stable prefix invariant.\n"
        "Diff:\n" + "\n".join(diffs_tools)
    )


def _extract_stable_prefix_hash(messages: list[dict], tools: list[dict]) -> str:  # type: ignore[type-arg]
    """Return a canonical hash of the stable prefix: system + tools + all non-last messages."""
    # Stable prefix = everything before the most recent user message
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    stable_msgs = messages[:-1] if messages else []
    key_obj = {
        "system": sys_msgs,
        "tools": sorted(tools, key=lambda t: t.get("function", {}).get("name", "")),
        "stable_messages": stable_msgs,
    }
    return _canonical(key_obj)


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason=(
        "cubepi and langgraph stable-prefix hashes diverge; M6 parity gate. "
        "Remove xfail once parity is achieved."
    ),
    strict=False,
)
async def test_byte_parity_stable_prefix_hashes(
    member_client: tuple,  # type: ignore[type-arg]
) -> None:
    """Compare the stable prefix hash across runtimes.

    For prompt cache to behave identically, the stable prefix
    (system message + tools + prior messages) sent on turn 2 must be
    byte-identical between langgraph and cubepi.

    This is the aggregate diagnostic: tests the exact conditions under
    which M5.3 (cache E2E) fails.
    """
    client, ws_id = member_client
    app = client._transport.app  # type: ignore[union-attr]

    # Run langgraph × 2 turns, then cubepi × 2 turns.
    # Each runtime gets its own conversation to avoid history bleed.
    hashes: dict[str, list[str]] = {}

    for runtime_name in ("langgraph", "cubepi"):
        resp = await client.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": f"byte-parity-prefix-{runtime_name}"},
        )
        resp.raise_for_status()
        conv_id = resp.json()["id"]

        app.state.agents_runtime = runtime_name
        runtime_hashes: list[str] = []

        try:
            for _turn in range(2):
                with respx.mock(assert_all_called=False) as router:
                    route = router.post(url__regex=r".*/chat/completions.*").mock(
                        return_value=httpx.Response(
                            200,
                            text=_MINIMAL_SSE,
                            headers={"content-type": "text/event-stream"},
                        )
                    )

                    await collect_sse_events(
                        client,
                        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
                        json_data={"content": "Reply with one word."},
                    )

                    if route.calls:
                        body = json.loads(route.calls.last.request.content.decode("utf-8"))
                        msgs = body.get("messages", [])
                        tools = body.get("tools", [])
                        prefix_hash = _extract_stable_prefix_hash(msgs, tools)
                        runtime_hashes.append(prefix_hash)
                    else:
                        runtime_hashes.append("<no-call>")
        finally:
            app.state.agents_runtime = None

        hashes[runtime_name] = runtime_hashes

    # Within each runtime, turn-2 stable prefix must be a superset of turn-1 prefix.
    # (turn-1 prefix = all msgs except user[0]; turn-2 prefix = all msgs except user[1])
    # The hash can differ because turn 2 has MORE history — that's expected.
    # What must NOT differ: the system message + tools canonical form.

    lg_hashes = hashes.get("langgraph", [])
    pi_hashes = hashes.get("cubepi", [])

    print(f"\nlanggraph prefix hashes: {lg_hashes}")
    print(f"cubepi prefix hashes:    {pi_hashes}")

    assert len(lg_hashes) == 2, f"Expected 2 langgraph hashes, got {lg_hashes}"
    assert len(pi_hashes) == 2, f"Expected 2 cubepi hashes, got {pi_hashes}"

    # Turn 1 stable prefix should match between runtimes (both start fresh,
    # only system + tools matter).
    assert lg_hashes[0] == pi_hashes[0], (
        f"Turn 1 stable prefix differs between langgraph and cubepi.\n"
        f"This means the system message or tools are assembled differently.\n"
        f"langgraph: {lg_hashes[0][:200]}\n"
        f"cubepi:    {pi_hashes[0][:200]}"
    )
