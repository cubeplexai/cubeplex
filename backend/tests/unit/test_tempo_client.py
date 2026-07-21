"""TempoClient unit tests (httpx mocked)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from cubeplex.api.schemas.trace import SpanKind
from cubeplex.services.tempo_client import TempoClient, TempoQueryError, TempoTraceNotFoundError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "tempo"


@pytest.fixture
def search_json() -> dict:
    return json.loads((FIXTURES / "sample_search.json").read_text())


@respx.mock
async def test_search_builds_traceql_with_filters(search_json: dict) -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    route = respx.get("http://tempo.local/api/search").mock(
        return_value=httpx.Response(200, json=search_json)
    )
    summaries = await client.search(
        org_id="org-1",
        workspace_id="ws-1",
        user_id=None,
        conversation_id="conv-9",
        run_id=None,
        model=None,
        start=datetime(2026, 6, 1, tzinfo=UTC),
        end=datetime(2026, 6, 11, tzinfo=UTC),
        limit=20,
    )
    assert route.called
    q = route.calls.last.request.url.params["q"]
    assert 'resource.service.name="cubeplex"' in q
    assert 'cubepi.metadata.org_id="org-1"' in q
    assert 'cubepi.metadata.workspace_id="ws-1"' in q
    assert 'cubepi.metadata.conversation_id="conv-9"' in q
    assert isinstance(summaries, list)


@respx.mock
async def test_search_raises_on_5xx() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(TempoQueryError):
        await client.search(org_id="org-1", limit=10)


async def test_search_rejects_injection_attempts() -> None:
    from cubeplex.services.tempo_client import TempoQueryValueError

    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    with pytest.raises(TempoQueryValueError):
        await client.search(
            org_id='ws-x" || true || span.foo="',
        )


@respx.mock
async def test_get_trace_returns_detail() -> None:
    payload = json.loads((FIXTURES / "sample_trace_multi_turn.json").read_text())
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/traces/abc123").mock(
        return_value=httpx.Response(200, json=payload)
    )
    detail = await client.get_trace("abc123")
    assert detail.summary.trace_id == payload["batches"][0]["scopeSpans"][0]["spans"][0]["traceId"]
    assert detail.root.children


@respx.mock
async def test_get_trace_extracts_agent_and_turn_payloads() -> None:
    """The invoke_agent (root) span and cubepi.turn spans carry their own
    gen_ai.input.messages/output.messages/system_instructions. This real
    captured fixture has some of these truncated mid-sentence (an actual
    tracing-pipeline artifact, not a synthetic edge case) - exercising both
    the happy path and the truncation sentinel in one fixture.
    """
    payload = json.loads((FIXTURES / "sample_trace_multi_turn.json").read_text())
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/traces/abc123").mock(
        return_value=httpx.Response(200, json=payload)
    )
    detail = await client.get_trace("abc123")

    agent = detail.root.agent
    assert agent is not None
    assert agent.provider == "unknown:deepseek-anthropic-shape"
    assert "datetime" in agent.tools
    # This fixture's agent-level system_instructions/messages are all
    # truncated (>1400 chars, cut off mid-string) - decode falls back to the
    # sentinel instead of silently returning [].
    assert len(agent.system_instructions) == 1
    assert agent.system_instructions[0].role == "_truncated"
    assert len(agent.messages) == 1
    assert agent.messages[0].role == "_truncated"

    turns = {t.turn.index: t.turn for t in detail.root.children if t.kind == SpanKind.TURN}
    # Turn 1's messages are short enough to be valid JSON - the happy path.
    assert turns[1].messages and turns[1].messages[0].role != "_truncated"
    assert turns[1].output_messages and turns[1].output_messages[0].role == "assistant"
    # Turn 0's are truncated - same sentinel fallback as the agent-level ones.
    assert turns[0].messages[0].role == "_truncated"
    assert turns[0].output_messages[0].role == "_truncated"


@respx.mock
async def test_chat_span_derives_output_messages_from_raw_response() -> None:
    """`gen_ai.output.messages` is never set on `chat` spans in this cubepi
    version (only on invoke_agent/cubepi.turn) - the chat span's own response
    content has to come from cubepi.llm.raw_response instead (OpenAI chat
    completions shape: choices[0].message.{content,tool_calls}).
    """
    raw_response = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {
                                    "name": "execute",
                                    "arguments": '{"command": "ls -la /workspace"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )
    payload = {
        "batches": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "abc123",
                                "spanId": "s1",
                                "name": "chat glm-5.2",
                                "startTimeUnixNano": "1781164911000000000",
                                "endTimeUnixNano": "1781164912000000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.operation.name",
                                        "value": {"stringValue": "chat"},
                                    },
                                    {
                                        "key": "cubepi.llm.raw_response",
                                        "value": {"stringValue": raw_response},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ],
    }
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/traces/abc123").mock(
        return_value=httpx.Response(200, json=payload)
    )
    detail = await client.get_trace("abc123")

    output = detail.root.llm.output_messages if detail.root.llm else None
    assert output is not None
    assert len(output) == 1
    assert output[0].role == "assistant"
    assert output[0].parts[0]["type"] == "tool_call"
    assert output[0].parts[0]["name"] == "execute"
    assert output[0].parts[0]["arguments"] == {"command": "ls -la /workspace"}


@respx.mock
async def test_tag_values_passes_through() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/v2/search/tag/span.cubepi.metadata.workspace_id/values").mock(
        return_value=httpx.Response(
            200,
            json={
                "tagValues": [
                    {"type": "string", "value": "ws-a"},
                    {"type": "string", "value": "ws-b"},
                ]
            },
        )
    )
    values = await client.tag_values(
        tag="cubepi.metadata.workspace_id",
        org_id="org-1",
    )
    assert values == ["ws-a", "ws-b"]


@respx.mock
async def test_tag_values_uses_v2_path_with_span_prefix_as_single_segment() -> None:
    """Regression for the round-3 mishap that built /api/v2/search/tag/span/<tag>/values
    (scope as its own path segment). Tempo expects the prefixed tag name as one
    segment — otherwise every autocomplete call 404s and the route returns 502.
    """
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    correct = respx.get(
        "http://tempo.local/api/v2/search/tag/span.cubepi.metadata.user_id/values"
    ).mock(return_value=httpx.Response(200, json={"tagValues": []}))
    wrong = respx.get(
        "http://tempo.local/api/v2/search/tag/span/cubepi.metadata.user_id/values"
    ).mock(return_value=httpx.Response(200, json={"tagValues": []}))
    await client.tag_values(tag="cubepi.metadata.user_id", org_id="org-1")
    assert correct.called, "tag_values must hit the single-segment v2 path"
    assert not wrong.called, "tag_values must NOT split scope into its own path segment"


@respx.mock
async def test_search_handles_null_traces() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search").mock(
        return_value=httpx.Response(200, json={"traces": None})
    )
    result = await client.search(org_id="org-1")
    assert result == []


@respx.mock
async def test_tag_values_handles_null_values() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/v2/search/tag/span.cubepi.metadata.workspace_id/values").mock(
        return_value=httpx.Response(200, json={"tagValues": None})
    )
    result = await client.tag_values(tag="cubepi.metadata.workspace_id", org_id="org-1")
    assert result == []


@respx.mock
async def test_tag_values_scopes_org_and_tag_as_sibling_spansets() -> None:
    """Regression: cubepi.metadata.org_id lives on the invoke_agent span while
    e.g. gen_ai.request.model lives on a child chat span. A single {...}
    selector requires both conditions on the same span and silently returns
    zero values (verified against a live Tempo instance). The org scope and
    the requested tag must be independent sibling spansets.
    """
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    route = respx.get("http://tempo.local/api/v2/search/tag/span.gen_ai.request.model/values").mock(
        return_value=httpx.Response(200, json={"tagValues": []})
    )
    await client.tag_values(tag="gen_ai.request.model", org_id="org-1")
    q = route.calls.last.request.url.params["q"]
    assert '{ resource.service.name="cubeplex" && span.cubepi.metadata.org_id="org-1" }' in q
    assert '{ span.gen_ai.request.model != "" }' in q


async def test_get_trace_rejects_invalid_trace_id() -> None:
    from cubeplex.services.tempo_client import TempoQueryValueError

    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    with pytest.raises(TempoQueryValueError):
        await client.get_trace("abc\ninjected")


@respx.mock
async def test_get_trace_wraps_parse_errors() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/traces/abc").mock(
        return_value=httpx.Response(200, text="not json")
    )
    with pytest.raises(TempoQueryError):
        await client.get_trace("abc")


@respx.mock
async def test_search_wraps_parse_errors() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search").mock(
        return_value=httpx.Response(200, text="not json")
    )
    with pytest.raises(TempoQueryError):
        await client.search(org_id="org-1")


@respx.mock
async def test_search_extracts_metadata_from_spansets() -> None:
    payload = {
        "traces": [
            {
                "traceID": "abc",
                "rootTraceName": "invoke_agent",
                "startTimeUnixNano": "1781164911000000000",
                "durationMs": 8055,
                "spanSet": {
                    "matched": 1,
                    "spans": [
                        {
                            "spanID": "s1",
                            "attributes": [
                                {
                                    "key": "cubepi.metadata.workspace_id",
                                    "value": {"stringValue": "ws-a"},
                                },
                                {
                                    "key": "cubepi.metadata.user_id",
                                    "value": {"stringValue": "usr-x"},
                                },
                                {
                                    "key": "cubepi.metadata.conversation_id",
                                    "value": {"stringValue": "conv-7"},
                                },
                                {"key": "cubepi.run_id", "value": {"stringValue": "run-99"}},
                            ],
                        }
                    ],
                },
            }
        ],
    }
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search").mock(return_value=httpx.Response(200, json=payload))
    [summary] = await client.search(org_id="org-1")
    assert summary.workspace_id == "ws-a"
    assert summary.user_id == "usr-x"
    assert summary.conversation_id == "conv-7"
    assert summary.run_id == "run-99"


@respx.mock
async def test_search_splits_model_into_sibling_spanset() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    route = respx.get("http://tempo.local/api/search").mock(
        return_value=httpx.Response(200, json={"traces": []})
    )
    await client.search(org_id="org-1", model="deepseek-v4-flash")
    q = route.calls.last.request.url.params["q"]
    # The model clause must be in its own selector to avoid same-span && match.
    assert 'gen_ai.request.model="deepseek-v4-flash"' in q
    # Two top-level selectors joined by &&:
    assert q.count("} &&") >= 1
    # Model must be included in the select() projection.
    assert "gen_ai.request.model" in q


@respx.mock
async def test_search_wraps_transport_errors() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(TempoQueryError):
        await client.search(org_id="org-1")


@respx.mock
async def test_get_trace_wraps_transport_errors() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/traces/abc").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(TempoQueryError):
        await client.get_trace("abc")


@respx.mock
async def test_search_extracts_model_from_sibling_spanset() -> None:
    """When the search has both metadata and model selectors, Tempo returns
    multiple spanSets and the model attribute lives in the second one."""
    payload = {
        "traces": [
            {
                "traceID": "abc",
                "rootTraceName": "invoke_agent",
                "startTimeUnixNano": "1781164911000000000",
                "durationMs": 8055,
                "spanSets": [
                    {
                        "matched": 1,
                        "spans": [
                            {
                                "spanID": "a1",
                                "attributes": [
                                    {
                                        "key": "cubepi.metadata.workspace_id",
                                        "value": {"stringValue": "ws-7"},
                                    },
                                ],
                            }
                        ],
                    },
                    {
                        "matched": 1,
                        "spans": [
                            {
                                "spanID": "c1",
                                "attributes": [
                                    {
                                        "key": "gen_ai.request.model",
                                        "value": {"stringValue": "deepseek-v4-flash"},
                                    },
                                ],
                            }
                        ],
                    },
                ],
                "serviceStats": {"cubeplex": {"spanCount": 3}},
            }
        ],
    }
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search").mock(return_value=httpx.Response(200, json=payload))
    [summary] = await client.search(org_id="org-1")
    assert summary.workspace_id == "ws-7"
    assert summary.model == "deepseek-v4-flash"
    # From serviceStats, not spanSet `matched` (which only counts spans that
    # matched the search selector, not the trace's real span total).
    assert summary.span_count == 3


@respx.mock
async def test_get_trace_raises_not_found_subclass() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/traces/abc").mock(
        return_value=httpx.Response(404, text="not found")
    )
    with pytest.raises(TempoTraceNotFoundError):
        await client.get_trace("abc")


def test_tempo_client_rejects_invalid_endpoint() -> None:
    with pytest.raises(ValueError):
        TempoClient(endpoint="http://", timeout_seconds=5)
    with pytest.raises(ValueError):
        TempoClient(endpoint="", timeout_seconds=5)
    with pytest.raises(ValueError):
        TempoClient(endpoint="http://tempo.local", timeout_seconds=0)


@respx.mock
async def test_search_reads_spansets_plural() -> None:
    # Tempo deployment that emits only the documented `spanSets` array (no legacy alias).
    payload = {
        "traces": [
            {
                "traceID": "abc",
                "rootTraceName": "invoke_agent",
                "startTimeUnixNano": "1781164911000000000",
                "durationMs": 8055,
                "spanSets": [
                    {
                        "matched": 1,
                        "spans": [
                            {
                                "spanID": "s1",
                                "attributes": [
                                    {
                                        "key": "cubepi.metadata.workspace_id",
                                        "value": {"stringValue": "ws-plural"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
                # NO spanSet (singular)
                "serviceStats": {"cubeplex": {"spanCount": 4}},
            }
        ],
    }
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search").mock(return_value=httpx.Response(200, json=payload))
    [summary] = await client.search(org_id="org-1")
    assert summary.workspace_id == "ws-plural"
    assert summary.span_count == 4


@respx.mock
async def test_search_span_count_defaults_to_zero_without_service_stats() -> None:
    """Older/malformed Tempo responses without serviceStats shouldn't crash -
    just report an unknown span count as 0 rather than falling back to the
    misleading `matched` count."""
    payload = {
        "traces": [
            {
                "traceID": "abc",
                "rootTraceName": "invoke_agent",
                "startTimeUnixNano": "1781164911000000000",
                "durationMs": 8055,
                "spanSet": {"matched": 1, "spans": []},
                # NO serviceStats
            }
        ],
    }
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search").mock(return_value=httpx.Response(200, json=payload))
    [summary] = await client.search(org_id="org-1")
    assert summary.span_count == 0
