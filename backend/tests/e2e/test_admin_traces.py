"""E2E for /api/v1/admin/traces routes."""

import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from cubeplex.api.routes.v1 import admin_traces
from cubeplex.api.schemas.trace import TraceSummary
from cubeplex.models import Conversation, Role, User, Workspace
from cubeplex.repositories import (
    MembershipRepository,
    OrganizationRepository,
    WorkspaceRepository,
)

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


async def test_list_defaults_to_last_hour_when_no_range_given(admin_client, fake_tempo) -> None:
    """Tempo's own default search window is much narrower than useful (it can
    return zero traces even when traces exist), so the route must never call
    Tempo without an explicit range."""
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces")
    assert resp.status_code == 200, resp.text
    call = fake_tempo.search.await_args
    start, end = call.kwargs["start"], call.kwargs["end"]
    assert start is not None and end is not None
    assert timedelta(minutes=59) < end - start < timedelta(hours=1, minutes=1)


async def test_list_fills_missing_side_of_a_partial_range(admin_client, fake_tempo) -> None:
    client, _ws = admin_client
    start = datetime.now(UTC) - timedelta(minutes=30)
    resp = await client.get("/api/v1/admin/traces", params={"start": start.isoformat()})
    assert resp.status_code == 200, resp.text
    call = fake_tempo.search.await_args
    assert call.kwargs["start"] == start
    assert call.kwargs["end"] is not None


async def test_list_rejects_range_over_168h(admin_client, fake_tempo) -> None:
    client, _ws = admin_client
    resp = await client.get(
        "/api/v1/admin/traces?start=2026-06-01T00:00:00%2B00:00&end=2026-07-01T00:00:00%2B00:00"
    )
    assert resp.status_code == 400
    assert "7 days" in resp.text
    fake_tempo.search.assert_not_awaited()


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


# --- filter-options (Postgres-backed dropdown suggestions) --------------------


@pytest_asyncio.fixture
async def seeded_filter_options(
    admin_client_with_user_id, db_session
) -> tuple[httpx.AsyncClient, dict[str, str]]:
    """Seed the admin's org + a foreign org with workspaces/conversations/users
    so filter-options scoping and prefix tests have something to assert against.

    Returns ``(client, labels)`` where ``labels`` maps a role to the seeded
    human-readable name the test asserts on.
    """
    client, ws_id, user_id = admin_client_with_user_id
    ws = await db_session.get(Workspace, ws_id)
    assert ws is not None
    org_id = ws.org_id

    token = secrets.token_hex(4)
    foreign_org = await OrganizationRepository(db_session).create(
        name=f"Foreign Org {token}", slug=f"foreign-{token}"
    )
    foreign_ws = await WorkspaceRepository(db_session).create(
        org_id=foreign_org.id, name="Foreign WS"
    )
    alpha_ws = await WorkspaceRepository(db_session).create(org_id=org_id, name="Alpha WS")

    # 3 alpha conversations so a `limit` cap is observable.
    alpha_conv_ids: list[str] = []
    for title in ("Alpha Chat A", "Alpha Chat B", "Alpha Chat C"):
        conv = Conversation(
            title=title,
            org_id=org_id,
            workspace_id=ws_id,
            creator_user_id=user_id,
            has_messages=True,
        )
        db_session.add(conv)
        alpha_conv_ids.append(conv.id)
    foreign_chat = Conversation(
        title="Foreign Chat",
        org_id=foreign_org.id,
        workspace_id=foreign_ws.id,
        creator_user_id=user_id,
        has_messages=True,
    )
    db_session.add(foreign_chat)

    alpha_user = User(
        email=f"alpha-{token}@example.com",
        hashed_password="x",
        display_name="Alpha User",
    )
    foreign_user = User(
        email=f"foreign-{token}@example.com",
        hashed_password="x",
        display_name="Foreign User",
    )
    db_session.add(alpha_user)
    db_session.add(foreign_user)
    await db_session.commit()

    await MembershipRepository(db_session).grant(
        user_id=alpha_user.id, workspace_id=ws_id, role=Role.MEMBER
    )
    await MembershipRepository(db_session).grant(
        user_id=foreign_user.id, workspace_id=foreign_ws.id, role=Role.MEMBER
    )

    return client, {
        "alpha_ws": "Alpha WS",
        "alpha_ws_id": alpha_ws.id,
        "foreign_ws": "Foreign WS",
        "foreign_ws_id": foreign_ws.id,
        "foreign_chat": "Foreign Chat",
        "foreign_chat_id": foreign_chat.id,
        "alpha_conv_id": alpha_conv_ids[0],
        "alpha_user": "Alpha User",
        "alpha_user_id": alpha_user.id,
        "foreign_user": "Foreign User",
        "foreign_user_id": foreign_user.id,
    }


async def test_filter_options_workspace_org_scoped(seeded_filter_options) -> None:
    client, labels = seeded_filter_options
    resp = await client.get("/api/v1/admin/traces/filter-options?kind=workspace")
    assert resp.status_code == 200, resp.text
    names = {o["name"] for o in resp.json()["options"]}
    assert labels["alpha_ws"] in names
    assert labels["foreign_ws"] not in names  # foreign org excluded


async def test_filter_options_conversation_prefix_and_scoped(seeded_filter_options) -> None:
    client, labels = seeded_filter_options
    resp = await client.get("/api/v1/admin/traces/filter-options?kind=conversation&q=Alpha")
    assert resp.status_code == 200, resp.text
    titles = {o["name"] for o in resp.json()["options"]}
    assert "Alpha Chat A" in titles
    assert labels["foreign_chat"] not in titles  # foreign org excluded


async def test_filter_options_conversation_limit_caps(seeded_filter_options) -> None:
    client, _labels = seeded_filter_options
    resp = await client.get("/api/v1/admin/traces/filter-options?kind=conversation&q=Alpha&limit=2")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["options"]) == 2  # 3 match, capped at limit


async def test_filter_options_ids_batch_lookup(seeded_filter_options) -> None:
    """`ids` resolves exact IDs to names (used to render names in the trace
    list table) instead of prefix-narrowing - and stays org-scoped like q."""
    client, labels = seeded_filter_options
    resp = await client.get(
        "/api/v1/admin/traces/filter-options",
        params={"kind": "workspace", "ids": [labels["alpha_ws_id"], labels["foreign_ws_id"]]},
    )
    assert resp.status_code == 200, resp.text
    names = {o["name"] for o in resp.json()["options"]}
    assert names == {labels["alpha_ws"]}  # foreign org's workspace excluded


async def test_filter_options_ids_wins_over_q(seeded_filter_options) -> None:
    client, labels = seeded_filter_options
    resp = await client.get(
        "/api/v1/admin/traces/filter-options",
        params={
            "kind": "conversation",
            "q": "nonexistent-prefix",
            "ids": [labels["alpha_conv_id"]],
        },
    )
    assert resp.status_code == 200, resp.text
    ids = {o["id"] for o in resp.json()["options"]}
    assert ids == {labels["alpha_conv_id"]}


async def test_filter_options_ids_rejects_oversized_batch(admin_client) -> None:
    client, _ws = admin_client
    resp = await client.get(
        "/api/v1/admin/traces/filter-options",
        params={"kind": "user", "ids": [f"usr-{i}" for i in range(201)]},
    )
    assert resp.status_code == 400


async def test_filter_options_user_prefix_and_scoped(seeded_filter_options) -> None:
    client, labels = seeded_filter_options
    resp = await client.get("/api/v1/admin/traces/filter-options?kind=user&q=Alpha")
    assert resp.status_code == 200, resp.text
    names = {o["name"] for o in resp.json()["options"]}
    assert labels["alpha_user"] in names
    assert labels["foreign_user"] not in names  # foreign org excluded via membership join


async def test_filter_options_rejects_unknown_kind(admin_client) -> None:
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/traces/filter-options?kind=robot")
    assert resp.status_code == 422  # FastAPI StrEnum query validation


async def test_filter_options_requires_admin(non_admin_client) -> None:
    resp = await non_admin_client.get("/api/v1/admin/traces/filter-options?kind=workspace")
    assert resp.status_code == 403
