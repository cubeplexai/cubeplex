"""E2E for /api/v1/admin/traces routes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from cubeplex.api.routes.v1 import admin_traces
from cubeplex.api.schemas.trace import TraceSummary

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


@pytest.fixture
def fake_resolve_org(monkeypatch):
    """Pin resolve_current_org_id to a known org id, auto-restored after the test."""
    from cubeplex.api.routes.v1 import admin_traces as mod

    async def _fake(*_a, **_kw):
        return "org-MATCH"

    monkeypatch.setattr(mod, "resolve_current_org_id", _fake)
    return "org-MATCH"


async def test_detail_returns_trace(admin_client, fake_tempo, fake_resolve_org) -> None:
    from cubeplex.api.schemas.trace import SpanKind, SpanNode, TraceDetail

    fake_tempo.get_trace.return_value = TraceDetail(
        summary=TraceSummary(
            trace_id="t1",
            root_name="invoke_agent",
            start_time=datetime(2026, 6, 11, tzinfo=UTC),
            duration_ms=1000,
            span_count=1,
            org_id="org-MATCH",
        ),
        root=SpanNode(
            span_id="s1",
            parent_span_id=None,
            name="invoke_agent",
            kind=SpanKind.AGENT,
            start_time=datetime(2026, 6, 11, tzinfo=UTC),
            duration_ms=1000,
            children=[],
        ),
    )
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces/t1")
    assert resp.status_code == 200
    assert resp.json()["summary"]["trace_id"] == "t1"


async def test_list_rejects_naive_datetime(admin_client, fake_tempo) -> None:
    client, _ws = admin_client
    resp = await client.get(
        "/api/v1/admin/traces?start=2026-06-11T09:00:00&end=2026-06-12T09:00:00"
    )
    assert resp.status_code == 400
    assert "timezone" in resp.text.lower()


async def test_detail_rejects_foreign_org_in_child_span(
    admin_client, fake_tempo, fake_resolve_org
) -> None:
    from cubeplex.api.schemas.trace import SpanKind, SpanNode, TraceDetail

    fake_tempo.get_trace.return_value = TraceDetail(
        summary=TraceSummary(
            trace_id="t1",
            root_name="invoke_agent",
            start_time=datetime(2026, 6, 11, tzinfo=UTC),
            duration_ms=1000,
            span_count=2,
            org_id="org-MATCH",  # summary looks fine
        ),
        root=SpanNode(
            span_id="s1",
            parent_span_id=None,
            name="invoke_agent",
            kind=SpanKind.AGENT,
            start_time=datetime(2026, 6, 11, tzinfo=UTC),
            duration_ms=1000,
            raw_attributes={"cubepi.metadata.org_id": "org-MATCH"},
            children=[
                SpanNode(
                    span_id="s2",
                    parent_span_id="s1",
                    name="chat",
                    kind=SpanKind.CHAT,
                    start_time=datetime(2026, 6, 11, tzinfo=UTC),
                    duration_ms=500,
                    raw_attributes={"cubepi.metadata.org_id": "org-EVIL"},  # foreign
                    children=[],
                ),
            ],
        ),
    )
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces/t1")
    assert resp.status_code == 404


async def test_detail_404_on_org_mismatch(admin_client, fake_tempo, fake_resolve_org) -> None:
    from cubeplex.api.schemas.trace import SpanKind, SpanNode, TraceDetail

    fake_tempo.get_trace.return_value = TraceDetail(
        summary=TraceSummary(
            trace_id="t1",
            root_name="invoke_agent",
            start_time=datetime(2026, 6, 11, tzinfo=UTC),
            duration_ms=1000,
            span_count=1,
            org_id="org-OTHER",
        ),
        root=SpanNode(
            span_id="s1",
            parent_span_id=None,
            name="invoke_agent",
            kind=SpanKind.AGENT,
            start_time=datetime(2026, 6, 11, tzinfo=UTC),
            duration_ms=1000,
            children=[],
        ),
    )
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces/t1")
    assert resp.status_code == 404
