"""TempoClient unit tests (httpx mocked)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from cubebox.services.tempo_client import TempoClient, TempoQueryError

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
    assert 'resource.service.name="cubebox"' in q
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
    from cubebox.services.tempo_client import TempoQueryValueError

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
async def test_tag_values_passes_through() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    respx.get("http://tempo.local/api/search/tag/cubepi.metadata.workspace_id/values").mock(
        return_value=httpx.Response(200, json={"tagValues": ["ws-a", "ws-b"]})
    )
    values = await client.tag_values(
        tag="cubepi.metadata.workspace_id",
        org_id="org-1",
    )
    assert values == ["ws-a", "ws-b"]


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
    respx.get("http://tempo.local/api/search/tag/cubepi.metadata.workspace_id/values").mock(
        return_value=httpx.Response(200, json={"tagValues": None})
    )
    result = await client.tag_values(tag="cubepi.metadata.workspace_id", org_id="org-1")
    assert result == []


@respx.mock
async def test_search_passes_zero_duration_filter() -> None:
    client = TempoClient(endpoint="http://tempo.local", timeout_seconds=5)
    route = respx.get("http://tempo.local/api/search").mock(
        return_value=httpx.Response(200, json={"traces": []})
    )
    await client.search(org_id="org-1", min_duration_ms=0)
    q = route.calls.last.request.url.params["q"]
    assert "trace:duration > 0ms" in q


async def test_get_trace_rejects_invalid_trace_id() -> None:
    from cubebox.services.tempo_client import TempoQueryValueError

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
