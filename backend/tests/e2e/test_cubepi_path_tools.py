"""E2E: cubepi path invokes a builtin tool (M2.6).

Prompts the model to use the calculator tool; asserts the SSE stream
contains tool_call + tool_result + final text. Confirms M2's full
tool-loading + dispatch is functional under real LLM.

Runtime: config.test.yaml sets agents.runtime = "cubepi" so no app.state
override is needed — the route is active for all E2E tests in this worktree.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import collect_sse_events

pytestmark = pytest.mark.real_llm


def _flatten_content(evt: dict) -> str:  # type: ignore[type-arg]
    """Extract plain text from a tool_result event's content.

    The cubeplex SSE envelope nests the payload inside 'data':
      {'type': 'tool_result', 'data': {'content': '1131', ...}, ...}
    Fall back to top-level 'content' / 'result' for forward-compat and
    handle list-of-blocks from providers that return structured content.
    """

    def _from_value(c: object) -> str | None:
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts: list[str] = []
            for block in c:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)
        return None

    # Prefer data.content (cubeplex SSE envelope)
    data = evt.get("data")
    if isinstance(data, dict):
        v = _from_value(data.get("content"))
        if v is not None:
            return v
        v = _from_value(data.get("result"))
        if v is not None:
            return v

    # Legacy / forward-compat: top-level content or result
    v = _from_value(evt.get("content"))
    if v is not None:
        return v
    v = _from_value(evt.get("result"))
    if v is not None:
        return v

    return repr(evt)


@pytest.mark.asyncio
async def test_cubepi_path_invokes_calculator_tool(
    member_client: tuple,  # type: ignore[type-arg]
) -> None:
    """POST a prompt that should trigger the calculator tool.

    Verifies the stream contains:
    - at least one tool_call event with name == "calculator"
    - at least one tool_result event whose content includes "1131"
    - at least one text_delta (model integrating the result)
    - a "done" terminator
    - zero error events
    """
    client, ws_id = member_client

    # 1. Create a conversation
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": "cubepi-m2-tool-smoke"},
    )
    assert resp.status_code == 201, f"conversation creation failed: {resp.text}"
    conv_id = resp.json()["id"]

    # 2. POST a message that must use the calculator tool
    prompt = (
        "You MUST call the calculator tool to compute 87 multiplied by 13. "
        "Do not compute it yourself — use the calculator tool, then state the result "
        "in one sentence."
    )
    events = await collect_sse_events(
        client,
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json_data={"content": prompt},
    )

    # 3. Assertions
    seen_types = [e.get("type") for e in events]

    # No error events
    errors = [e for e in events if e.get("type") == "error"]
    assert not errors, f"unexpected error events: {errors!r}"

    # "done" terminator present
    assert "done" in seen_types, f"no 'done' event in stream; seen: {seen_types!r}"

    # At least one tool_call for the calculator.
    # The SSE schema nests tool identity under a 'data' sub-dict:
    #   {'type': 'tool_call', 'data': {'name': 'calculator', 'arguments': {...}}, ...}
    # Fall back to top-level 'name' for forward-compatibility.
    def _tool_call_name(evt: dict) -> str:  # type: ignore[type-arg]
        data = evt.get("data")
        if isinstance(data, dict):
            return str(data.get("name", ""))
        return str(evt.get("name", ""))

    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    calc_calls = [e for e in tool_calls if _tool_call_name(e) == "calculator"]
    assert calc_calls, (
        f"no calculator tool_call event in stream.\n"
        f"  tool_call events seen: {tool_calls!r}\n"
        f"  all event types: {seen_types!r}"
    )

    # At least one tool_result event
    tool_results = [e for e in events if e.get("type") == "tool_result"]
    assert tool_results, f"no tool_result event in stream: {seen_types!r}"

    # The calculator must return 1131 (87 * 13)
    matched = any("1131" in _flatten_content(e) for e in tool_results)
    assert matched, "expected '1131' in some tool_result content; got:\n" + "\n".join(
        repr(e) for e in tool_results
    )

    # At least one text_delta (model summarised the result)
    text_deltas = [e for e in events if e.get("type") == "text_delta"]
    assert text_deltas, f"no text_delta events in stream: {seen_types!r}"
