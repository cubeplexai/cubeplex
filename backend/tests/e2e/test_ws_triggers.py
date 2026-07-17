"""E2E tests for workspace trigger CRUD + events + replay + rotate-secret routes.

TDD: tests written first; they fail with 404/500 until the routes are wired.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio

from cubeplex.models import Role
from tests.e2e.conftest import _lifespan_context, _make_isolated_user

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ws_client_a() -> Any:
    """Fresh admin + fresh workspace A."""
    app, email, password, ws_id = await _make_isolated_user(Role.ADMIN)
    app.state.deployment_mode = "multi_tenant"
    from tests.e2e.conftest import _login_and_attach

    async with _lifespan_context(app):
        import httpx as _httpx

        transport = _httpx.ASGITransport(app=app)
        async with _httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, ws_id


@pytest_asyncio.fixture
async def ws_client_b() -> Any:
    """Fresh admin + fresh workspace B (different user, org, workspace)."""
    app, email, password, ws_id = await _make_isolated_user(Role.ADMIN)
    app.state.deployment_mode = "multi_tenant"
    from tests.e2e.conftest import _login_and_attach

    async with _lifespan_context(app):
        import httpx as _httpx

        transport = _httpx.ASGITransport(app=app)
        async with _httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, ws_id


async def _get_my_user_id(client: httpx.AsyncClient) -> str:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 200
    return r.json()["id"]


async def _create_trigger(
    client: httpx.AsyncClient,
    ws_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    """Create a trigger with sensible defaults; returns parsed JSON."""
    user_id = await _get_my_user_id(client)
    body: dict[str, Any] = {
        "name": "test-trigger",
        "webhook_secret": "s3cret",
        "prompt_template": "hi {{ event.action }}",
        "payload_fields": ["event.action"],
        "run_as_user_id": user_id,
    }
    body.update(overrides)
    r = await client.post(f"/api/v1/ws/{ws_id}/triggers", json=body)
    return r.json() if r.status_code in (200, 201) else {"_status": r.status_code, "_body": r.text}


# ---------------------------------------------------------------------------
# CRUD round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crud_round_trip(authenticated_client: tuple[httpx.AsyncClient, str]) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)

    # POST → create
    r_create = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "crud-trigger",
            "webhook_secret": "s3cret",
            "prompt_template": "hello {{ event.action }}",
            "payload_fields": ["event.action"],
            "run_as_user_id": user_id,
        },
    )
    assert r_create.status_code == 201, r_create.text
    data = r_create.json()
    trig_id = data["id"]

    # Shape checks
    assert data["name"] == "crud-trigger"
    assert data["enabled"] is True
    assert data["source_type"] == "webhook"
    assert data["target_type"] == "inline"
    assert data["events_total"] == 0
    assert data["events_success"] == 0
    assert data["events_failed"] == 0
    assert data["events_dedup_dropped"] == 0
    assert "webhook_secret" not in data  # never echo plaintext
    assert "current_secret_cred_id" in data

    # GET list — trigger visible
    r_list = await client.get(f"/api/v1/ws/{ws_id}/triggers")
    assert r_list.status_code == 200
    ids = [t["id"] for t in r_list.json()["triggers"]]
    assert trig_id in ids

    # GET detail
    r_get = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trig_id}")
    assert r_get.status_code == 200
    detail = r_get.json()
    assert detail["id"] == trig_id
    assert "webhook_secret" not in detail

    # PATCH — disable
    r_patch = await client.patch(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}",
        json={"enabled": False},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["enabled"] is False

    # List still returns it (disabled, not deleted)
    r_list2 = await client.get(f"/api/v1/ws/{ws_id}/triggers")
    ids2 = [t["id"] for t in r_list2.json()["triggers"]]
    assert trig_id in ids2

    # DELETE
    r_del = await client.delete(f"/api/v1/ws/{ws_id}/triggers/{trig_id}")
    assert r_del.status_code == 204

    # GET /{id} → 404
    r_gone = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trig_id}")
    assert r_gone.status_code == 404


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_isolation(
    ws_client_a: tuple[httpx.AsyncClient, str],
    ws_client_b: tuple[httpx.AsyncClient, str],
) -> None:
    client_a, ws_a = ws_client_a
    client_b, ws_b = ws_client_b

    # Create in WS-A
    created = await _create_trigger(client_a, ws_a, name="ws-a-trigger")
    assert "id" in created, f"create failed: {created}"

    # WS-B list → empty
    r = await client_b.get(f"/api/v1/ws/{ws_b}/triggers")
    assert r.status_code == 200
    assert r.json()["triggers"] == []


# ---------------------------------------------------------------------------
# Validation — non-member run_as_user_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_member_run_as_user_id(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "bad-trigger",
            "webhook_secret": "s3cret",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": "usr-NOTAMEMBER",
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Validation — conversation_policy literal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_conversation_policy(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "bad-policy",
            "webhook_secret": "s3cret",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
            "conversation_policy": "pinned",
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Validation — rate_limit_response literal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_rate_limit_response(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "bad-rl",
            "webhook_secret": "s3cret",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
            "rate_limit_response": "999",
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Rotate secret — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_secret_happy_path(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)

    # Create
    r_create = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "rotate-trigger",
            "webhook_secret": "oldsecret",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
        },
    )
    assert r_create.status_code == 201
    trig_id = r_create.json()["id"]
    original_cred_id = r_create.json()["current_secret_cred_id"]

    # Rotate
    overlap = 3600
    r_rot = await client.post(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}/rotate-secret",
        json={"new_webhook_secret": "newsecret", "overlap_seconds": overlap},
    )
    assert r_rot.status_code == 200
    rot_data = r_rot.json()
    assert "previous_secret_expires_at" in rot_data
    assert "current_secret_cred_id" in rot_data
    assert rot_data["previous_secret_expires_at"] is not None

    # GET detail — previous fields populated
    r_get = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trig_id}")
    assert r_get.status_code == 200
    d = r_get.json()
    assert d["previous_secret_cred_id"] == original_cred_id
    assert d["previous_secret_cred_id"] is not None
    assert d["previous_secret_expires_at"] is not None

    # previous_secret_expires_at ≈ now + overlap_seconds (within 30s tolerance)
    expires_at = datetime.fromisoformat(d["previous_secret_expires_at"])
    now = datetime.now(UTC)
    diff = abs((expires_at - now).total_seconds() - overlap)
    assert diff < 30, f"expires_at mismatch: diff={diff}s"


# ---------------------------------------------------------------------------
# Rotate secret — overlap_seconds=0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_secret_zero_overlap(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)

    r_create = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "rotate-zero",
            "webhook_secret": "oldsecret",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
        },
    )
    assert r_create.status_code == 201
    trig_id = r_create.json()["id"]

    r_rot = await client.post(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}/rotate-secret",
        json={"new_webhook_secret": "newsecret", "overlap_seconds": 0},
    )
    assert r_rot.status_code == 200

    r_get = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trig_id}")
    d = r_get.json()
    assert d["previous_secret_expires_at"] is not None
    expires_at = datetime.fromisoformat(d["previous_secret_expires_at"])
    # Expiry should be ≤ now (already expired, old secret no longer valid)
    assert expires_at <= datetime.now(UTC)


# ---------------------------------------------------------------------------
# Back-to-back rotate must not collide on the vault unique constraint
# (codex P2 — same-second epoch suffix used to collide).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_secret_back_to_back(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)

    r_create = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "rotate-twice",
            "webhook_secret": "s0",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
        },
    )
    assert r_create.status_code == 201
    trig_id = r_create.json()["id"]

    # Two rotates in the same logical instant. Both must succeed even though
    # they share the same trigger id and (effectively) the same wall clock.
    r1 = await client.post(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}/rotate-secret",
        json={"new_webhook_secret": "s1"},
    )
    assert r1.status_code == 200, r1.text
    r2 = await client.post(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}/rotate-secret",
        json={"new_webhook_secret": "s2"},
    )
    assert r2.status_code == 200, r2.text
    assert r1.json()["current_secret_cred_id"] != r2.json()["current_secret_cred_id"]


# ---------------------------------------------------------------------------
# Replay — non-dead-lettered event → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_non_dead_lettered_event(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)

    # Create trigger
    r_create = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "replay-trigger",
            "webhook_secret": "s3cret",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
        },
    )
    assert r_create.status_code == 201
    trig_id = r_create.json()["id"]

    # Insert a TriggerEvent with status="accepted" directly via DB
    from cubeplex.models import TriggerEvent

    # We need the org_id for the trigger — get from the detail
    r_detail = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trig_id}")
    assert r_detail.status_code == 200

    # Use async_session_maker to insert a trigger_event row
    import cubeplex.db as _db

    async with _db.async_session_maker() as session:
        # Get org_id for this workspace
        from sqlalchemy import select

        from cubeplex.models import Workspace

        ws_row = await session.execute(select(Workspace).where(Workspace.id == ws_id))
        ws_obj = ws_row.scalar_one()
        org_id = ws_obj.org_id

        event_row = TriggerEvent(
            trigger_id=trig_id,
            org_id=org_id,
            workspace_id=ws_id,
            source_type="webhook",
            dedup_key="test-dedup-key-replay",
            status="accepted",
            payload={},
        )
        session.add(event_row)
        await session.commit()
        await session.refresh(event_row)
        eid = event_row.id

    # POST replay → 409 because status != dead_lettered
    r_replay = await client.post(f"/api/v1/ws/{ws_id}/triggers/{trig_id}/events/{eid}/replay")
    assert r_replay.status_code == 409
    assert "dead_lettered" in r_replay.json()["detail"]


# ---------------------------------------------------------------------------
# Events list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_list(authenticated_client: tuple[httpx.AsyncClient, str]) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)

    # Create trigger
    r_create = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "events-trigger",
            "webhook_secret": "s3cret",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
        },
    )
    assert r_create.status_code == 201
    trig_id = r_create.json()["id"]

    # Events list starts empty
    r_events = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trig_id}/events")
    assert r_events.status_code == 200
    assert r_events.json()["events"] == []

    # Insert an event
    from sqlalchemy import select

    import cubeplex.db as _db
    from cubeplex.models import TriggerEvent, Workspace

    async with _db.async_session_maker() as session:
        ws_row = await session.execute(select(Workspace).where(Workspace.id == ws_id))
        ws_obj = ws_row.scalar_one()
        org_id = ws_obj.org_id
        event_row = TriggerEvent(
            trigger_id=trig_id,
            org_id=org_id,
            workspace_id=ws_id,
            source_type="webhook",
            dedup_key="test-dedup-events-list",
            status="accepted",
            payload={"hello": "world"},
        )
        session.add(event_row)
        await session.commit()
        await session.refresh(event_row)
        eid = event_row.id

    r_events2 = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trig_id}/events")
    assert r_events2.status_code == 200
    events = r_events2.json()["events"]
    assert len(events) == 1
    assert events[0]["id"] == eid
    assert events[0]["status"] == "accepted"

    # Filter by status
    r_filtered = await client.get(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}/events?status=dead_lettered"
    )
    assert r_filtered.status_code == 200
    assert r_filtered.json()["events"] == []


# ---------------------------------------------------------------------------
# Status filter must apply BEFORE pagination (regression on codex P2).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_filter_pages_across_unmatched_rows(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    """A `status=` filter must page through filtered events, not the raw window.

    Previously the route fetched `limit=N` rows then filtered status in Python.
    When the first N rows of a trigger had no matching status, the filtered
    response was empty even when matching events existed further back.
    """
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)

    r_create = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "filter-pagination",
            "webhook_secret": "s3cret",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
        },
    )
    assert r_create.status_code == 201
    trig_id = r_create.json()["id"]

    from sqlalchemy import select

    import cubeplex.db as _db
    from cubeplex.models import TriggerEvent, Workspace

    async with _db.async_session_maker() as session:
        ws_row = await session.execute(select(Workspace).where(Workspace.id == ws_id))
        org_id = ws_row.scalar_one().org_id
        # 5 'accepted' rows + 1 'dead_lettered' row.
        for i in range(5):
            session.add(
                TriggerEvent(
                    trigger_id=trig_id,
                    org_id=org_id,
                    workspace_id=ws_id,
                    source_type="webhook",
                    dedup_key=f"f-pag-acc-{i}",
                    status="accepted",
                    payload={},
                )
            )
        session.add(
            TriggerEvent(
                trigger_id=trig_id,
                org_id=org_id,
                workspace_id=ws_id,
                source_type="webhook",
                dedup_key="f-pag-dl",
                status="dead_lettered",
                payload={},
            )
        )
        await session.commit()

    # With limit=3, the first page in received_at-desc order is dominated by
    # accepted rows. The filtered-then-paginated query must still surface the
    # dead_lettered row.
    r = await client.get(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}/events?status=dead_lettered&limit=3&offset=0"
    )
    assert r.status_code == 200
    rows = r.json()["events"]
    assert len(rows) == 1
    assert rows[0]["status"] == "dead_lettered"


# ---------------------------------------------------------------------------
# Duplicate trigger names in the same workspace must not collide on the
# credential vault's (org_id, kind, name) unique constraint (codex P2).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_trigger_name_does_not_collide_on_vault(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)
    body = {
        "name": "duplicate-name",
        "webhook_secret": "s",
        "prompt_template": "hi",
        "payload_fields": [],
        "run_as_user_id": user_id,
    }
    r1 = await client.post(f"/api/v1/ws/{ws_id}/triggers", json=body)
    assert r1.status_code == 201, r1.text
    r2 = await client.post(f"/api/v1/ws/{ws_id}/triggers", json=body)
    assert r2.status_code == 201, r2.text
    assert r1.json()["id"] != r2.json()["id"]


# ---------------------------------------------------------------------------
# Mutating routes are admin-gated (codex P1).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_cannot_mutate_triggers(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """A non-admin workspace member must not create / update / delete triggers."""
    client, ws_id = member_client
    user_id = await _get_my_user_id(client)

    # POST create → 403
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "blocked",
            "webhook_secret": "s",
            "prompt_template": "x",
            "payload_fields": [],
            "run_as_user_id": user_id,
        },
    )
    assert r.status_code == 403, r.text

    # PATCH / DELETE / rotate-secret / replay all require admin too — even
    # against a non-existent id, the auth gate is first.
    r = await client.patch(
        f"/api/v1/ws/{ws_id}/triggers/trig-NOPE",
        json={"enabled": False},
    )
    assert r.status_code == 403, r.text

    r = await client.delete(f"/api/v1/ws/{ws_id}/triggers/trig-NOPE")
    assert r.status_code == 403, r.text

    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers/trig-NOPE/rotate-secret",
        json={"new_webhook_secret": "n"},
    )
    assert r.status_code == 403, r.text

    r = await client.post(f"/api/v1/ws/{ws_id}/triggers/trig-NOPE/events/trev-NOPE/replay")
    assert r.status_code == 403, r.text

    # GETs remain accessible to members (read-only).
    r = await client.get(f"/api/v1/ws/{ws_id}/triggers")
    assert r.status_code == 200, r.text
    assert r.json()["triggers"] == []


# ---------------------------------------------------------------------------
# Destination fields — conversation_policy is locked post-create.
# Mirrors the scheduled-task behavior: any attempt to change a destination
# field via PATCH must return 422, including explicit `null` (gated through
# `model_fields_set`).
# ---------------------------------------------------------------------------


async def _create_topic(client: httpx.AsyncClient, ws_id: str, title: str) -> str:
    r = await client.post(f"/api/v1/ws/{ws_id}/topics", json={"title": title})
    assert r.status_code in (200, 201), r.text
    tid = r.json()["topic"]["id"]
    assert isinstance(tid, str) and tid.startswith("top")
    return tid


async def _seed_im_account(
    client: httpx.AsyncClient,
    ws_id: str,
    external_account_id: str | None = None,
) -> str:
    """Seed one IMConnectorAccount row directly via DB and return its id.

    triggers.im_account_id is FK→im_connector_accounts.id with a NOT-NULL
    credential FK, so we can't create accounts purely via the HTTP layer
    without going through the full Feishu OAuth bootstrap. The tests in
    this module only need the row to exist for the FK check; the IM
    runtime is exercised separately. We stamp a credentials row directly
    via SQL (mirroring `im_fixtures.im_seed_stub_credential`) — no decrypt
    path is exercised here.

    The (platform, external_account_id) constraint is global, not
    org-scoped, so callers that don't supply an external_account_id get a
    random suffix to keep tests rerunnable against a non-wiped DB.
    """
    import secrets

    from sqlalchemy import select, text

    import cubeplex.db as _db
    from cubeplex.models import IMConnectorAccount, Workspace
    from cubeplex.models.public_id import generate_public_id

    if external_account_id is None:
        external_account_id = f"ext-{secrets.token_hex(8)}"
    else:
        external_account_id = f"{external_account_id}-{secrets.token_hex(4)}"

    user_id = await _get_my_user_id(client)
    cred_id = generate_public_id("cred")
    async with _db.async_session_maker() as session:
        ws = (await session.execute(select(Workspace).where(Workspace.id == ws_id))).scalar_one()
        await session.execute(
            text(
                "INSERT INTO credentials (id, org_id, kind, name, value_encrypted,"
                " cred_metadata, created_by_user_id, created_at, updated_at)"
                " VALUES (:id, :org, 'im_bot', :name, '\\x00'::bytea,"
                " '{}'::jsonb, :uid, NOW(), NOW())"
            ),
            {
                "id": cred_id,
                "org": ws.org_id,
                "name": f"im-account:{external_account_id}:{cred_id}",
                "uid": user_id,
            },
        )
        account = IMConnectorAccount(
            org_id=ws.org_id,
            workspace_id=ws_id,
            platform="feishu",
            external_account_id=external_account_id,
            acting_user_id=user_id,
            credential_id=cred_id,
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        return account.id


@pytest.mark.asyncio
async def test_create_im_channel_trigger_round_trips(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)
    im_id = await _seed_im_account(client, ws_id, "ext-rt-1")
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "im-trigger",
            "webhook_secret": "s3cret",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
            "conversation_policy": "im_channel",
            "im_account_id": im_id,
            "im_channel_id": "C-abc",
            "im_scope_key": "dm",
            "im_scope_kind": "dm",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["conversation_policy"] == "im_channel"
    assert body["im_account_id"] == im_id
    assert body["im_channel_id"] == "C-abc"


@pytest.mark.asyncio
async def test_create_im_channel_missing_field_422(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Spec validation runs before FK validation, so we don't need a real account."""
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "im-bad",
            "webhook_secret": "s",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
            "conversation_policy": "im_channel",
            "im_account_id": "ima_1",
            # missing im_channel_id / im_scope_key / im_scope_kind
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_rejects_conversation_policy_change(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    created = await _create_trigger(client, ws_id, name="pcp")
    trig_id = created["id"]
    r = await client.patch(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}",
        json={"conversation_policy": "im_channel"},
    )
    assert r.status_code == 422
    assert "conversation_policy" in r.text.lower()


@pytest.mark.asyncio
async def test_patch_rejects_im_field_change(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    created = await _create_trigger(client, ws_id, name="pim")
    trig_id = created["id"]
    r = await client.patch(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}",
        json={"im_account_id": "ima_x"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_rejects_explicit_null_conversation_policy(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    """`null` for a locked field must also 422 — model_fields_set tracks intent."""
    client, ws_id = authenticated_client
    created = await _create_trigger(client, ws_id, name="pnull")
    trig_id = created["id"]
    r = await client.patch(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}",
        json={"conversation_policy": None},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_topic_id_only_when_new_each_time(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client

    # new_each_time → topic_id PATCH allowed.
    topic = await _create_topic(client, ws_id, "pt-allowed")
    created = await _create_trigger(client, ws_id, name="ptnew")
    trig_id = created["id"]
    r = await client.patch(
        f"/api/v1/ws/{ws_id}/triggers/{trig_id}",
        json={"topic_id": topic},
    )
    assert r.status_code == 200, r.text
    assert r.json()["topic_id"] == topic

    # im_channel → topic_id PATCH refused.
    user_id = await _get_my_user_id(client)
    im_account = await _seed_im_account(client, ws_id, "ext-pt-1")
    r2 = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "im-policy",
            "webhook_secret": "s",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
            "conversation_policy": "im_channel",
            "im_account_id": im_account,
            "im_channel_id": "C-2",
            "im_scope_key": "ch",
            "im_scope_kind": "channel",
        },
    )
    assert r2.status_code == 201, r2.text
    im_id = r2.json()["id"]
    r3 = await client.patch(
        f"/api/v1/ws/{ws_id}/triggers/{im_id}",
        json={"topic_id": topic},
    )
    assert r3.status_code == 422


@pytest.mark.asyncio
async def test_list_filters_by_im_channel_id(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    user_id = await _get_my_user_id(client)
    im_account = await _seed_im_account(client, ws_id, "ext-filter-1")

    async def _im(name: str, channel: str) -> str:
        r = await client.post(
            f"/api/v1/ws/{ws_id}/triggers",
            json={
                "name": name,
                "webhook_secret": "s",
                "prompt_template": "hi",
                "payload_fields": [],
                "run_as_user_id": user_id,
                "conversation_policy": "im_channel",
                "im_account_id": im_account,
                "im_channel_id": channel,
                "im_scope_key": "ch",
                "im_scope_kind": "channel",
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    a = await _im("filt-a", "C-aaa")
    b = await _im("filt-b", "C-bbb")
    r = await client.get(
        f"/api/v1/ws/{ws_id}/triggers",
        params={"im_channel_id": "C-aaa"},
    )
    assert r.status_code == 200
    ids = {t["id"] for t in r.json()["triggers"]}
    assert a in ids
    assert b not in ids
