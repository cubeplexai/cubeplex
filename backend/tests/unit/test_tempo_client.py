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
