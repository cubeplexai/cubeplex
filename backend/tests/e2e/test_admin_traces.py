"""E2E for /api/v1/admin/traces routes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from cubebox.api.routes.v1 import admin_traces
from cubebox.api.schemas.trace import TraceSummary

pytestmark = pytest.mark.e2e


@pytest.fixture
def fake_tempo(monkeypatch) -> AsyncMock:
    """Replace the TempoClient factory with a mock returning canned data."""
    client = AsyncMock()
    client.search.return_value = [
        TraceSummary(
            trace_id="t1",
            root_name="invoke_agent",
            start_time=datetime(2026, 6, 11, tzinfo=UTC),
            duration_ms=2300,
            span_count=5,
        ),
    ]
    client.tag_values.return_value = ["ws-a", "ws-b"]
    monkeypatch.setattr(admin_traces, "get_tempo_client", lambda: client)
    return client


async def test_list_traces_requires_admin(admin_client, fake_tempo) -> None:
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["traces"][0]["trace_id"] == "t1"


async def test_list_injects_org_id(admin_client, fake_tempo) -> None:
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces?workspace_id=ws-a")
    assert resp.status_code == 200
    call = fake_tempo.search.await_args
    assert call.kwargs["org_id"].startswith("org-")
    assert call.kwargs["workspace_id"] == "ws-a"


async def test_list_returns_503_when_tempo_unset(admin_client, monkeypatch) -> None:
    monkeypatch.setattr(admin_traces, "get_tempo_client", lambda: None)
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces")
    assert resp.status_code == 503


async def test_tag_values_whitelist(admin_client, fake_tempo) -> None:
    client, _ws = admin_client
    ok = await client.get("/api/v1/admin/traces/tag-values?tag=cubepi.metadata.workspace_id")
    assert ok.status_code == 200
    bad = await client.get("/api/v1/admin/traces/tag-values?tag=secret.bearer")
    assert bad.status_code == 400
