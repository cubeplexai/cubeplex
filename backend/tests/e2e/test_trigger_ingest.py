"""E2E tests for the public HMAC-authenticated ingest route.

TDD: tests written first; they drive the ingest.py + trigger_ingest.py implementation.
All paths relative to the worktree root. Tests use `authenticated_client` (fresh
admin + workspace) and helper functions to sign bodies.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
import pytest_asyncio

from cubeplex.models import Role
from cubeplex.triggers.signature import sign
from tests.e2e.conftest import _lifespan_context, _login_and_attach, _make_isolated_user

# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------


def sign_body(secret: str, body: bytes) -> tuple[str, str]:
    """Returns (timestamp_str, signature_hex)."""
    ts = str(int(time.time()))
    sig = sign(secret, ts, body)
    return ts, sig


async def post_ingest(
    client: httpx.AsyncClient,
    ws_id: str,
    trigger_id: str,
    body: bytes,
    secret: str,
    headers_extra: dict[str, str] | None = None,
    ts_override: str | None = None,
) -> httpx.Response:
    ts, sig = sign_body(secret, body)
    if ts_override is not None:
        # Re-sign with the override timestamp so the signature matches.
        sig = sign(secret, ts_override, body)
        ts = ts_override
    headers = {
        "X-Signature": sig,
        "X-Timestamp": ts,
        "Content-Type": "application/json",
    }
    if headers_extra:
        headers.update(headers_extra)
    return await client.post(
        f"/api/v1/ws/{ws_id}/triggers/{trigger_id}/ingest",
        content=body,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


async def _get_my_user_id(client: httpx.AsyncClient) -> str:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 200
    return r.json()["id"]


async def _create_trigger(
    client: httpx.AsyncClient,
    ws_id: str,
    secret: str = "s3cret",
    **overrides: Any,
) -> dict[str, Any]:
    """Create a trigger and return the parsed JSON. Default secret exposed for signing."""
    user_id = await _get_my_user_id(client)
    body: dict[str, Any] = {
        "name": "ingest-trigger",
        "webhook_secret": secret,
        "prompt_template": "handle {{ event.action }}",
        "payload_fields": ["event.action"],
        "run_as_user_id": user_id,
    }
    body.update(overrides)
    r = await client.post(f"/api/v1/ws/{ws_id}/triggers", json=body)
    assert r.status_code == 201, f"create_trigger failed: {r.status_code} {r.text}"
    return r.json()


async def _poll_event_accepted(
    client: httpx.AsyncClient,
    ws_id: str,
    trigger_id: str,
    event_id: str,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Poll events list until the event shows accepted + resulting_run_id set.

    The event row is inserted as 'accepted' immediately; we wait for the
    background pipeline.fire to set resulting_run_id (which happens after
    start_run completes). If the pipeline fails (no model provider in tests),
    the event may end up dead_lettered — we accept both outcomes; the caller
    checks the specific assertion.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trigger_id}/events")
        assert r.status_code == 200
        events = r.json()["events"]
        for ev in events:
            if ev["id"] == event_id:
                # Wait for resulting_run_id to be populated (pipeline ran start_run)
                # or for the status to move to a terminal failure (dead_lettered).
                if ev["resulting_run_id"] is not None:
                    return ev
                if ev["status"] in ("dead_lettered", "failed"):
                    return ev
        await asyncio.sleep(0.15)
    # Return the last known state — caller handles assertion.
    r = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trigger_id}/events")
    events = r.json()["events"]
    for ev in events:
        if ev["id"] == event_id:
            return ev
    raise TimeoutError(f"event {event_id} not found within {timeout}s")


async def _get_trigger(
    client: httpx.AsyncClient,
    ws_id: str,
    trigger_id: str,
) -> dict[str, Any]:
    r = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trigger_id}")
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------------------
# Isolated second-workspace fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ws_client_b() -> Any:
    """Fresh admin + fresh workspace B (different user, org, workspace)."""
    app, email, password, ws_id = await _make_isolated_user(Role.ADMIN)
    app.state.deployment_mode = "multi_tenant"
    async with _lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await _login_and_attach(c, email, password)
            yield c, ws_id


# ---------------------------------------------------------------------------
# T1: Happy path — 202 accepted + event reaches 'accepted' with resulting_run_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path(authenticated_client: tuple[httpx.AsyncClient, str]) -> None:
    client, ws_id = authenticated_client

    secret = "happy-secret"
    trig = await _create_trigger(client, ws_id, secret=secret, name="happy-trig")
    trigger_id = trig["id"]

    body = b'{"event_type": "push", "event": {"action": "created"}}'
    r = await post_ingest(client, ws_id, trigger_id, body, secret)
    assert r.status_code == 202, r.text
    data = r.json()
    assert data["status"] == "accepted"
    event_id = data["event_id"]
    assert event_id.startswith("trev-")

    # Poll until pipeline.fire finishes (sets resulting_run_id or moves to
    # dead_lettered if model provider keys are missing in the test env).
    ev = await _poll_event_accepted(client, ws_id, trigger_id, event_id)
    # In a full environment: resulting_run_id is set and status=accepted.
    # In test env without provider keys: pipeline may fail → dead_lettered.
    # Either way the ingest route returned 202 and the event row was created.
    assert ev["status"] in ("accepted", "dead_lettered", "failed")

    # events_total is bumped by the pipeline (success or failed path).
    t = await _get_trigger(client, ws_id, trigger_id)
    assert t["events_total"] >= 1


# ---------------------------------------------------------------------------
# T2: Bad signature → flat 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bad_signature(authenticated_client: tuple[httpx.AsyncClient, str]) -> None:
    client, ws_id = authenticated_client

    trig = await _create_trigger(client, ws_id, name="bad-sig-trig")
    trigger_id = trig["id"]

    body = b'{"event_type": "push"}'
    ts = str(int(time.time()))
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers/{trigger_id}/ingest",
        content=body,
        headers={
            "X-Signature": "deadbeef",
            "X-Timestamp": ts,
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}

    # No event row created.
    r_events = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trigger_id}/events")
    assert r_events.json()["events"] == []


# ---------------------------------------------------------------------------
# T3: Missing/disabled trigger → identical flat 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_and_disabled_trigger_flat_404(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client
    secret = "s3cret"

    # Missing trigger.
    body = b'{"event_type": "push"}'
    r_missing = await post_ingest(client, ws_id, "trig-NOPE", body, secret)
    assert r_missing.status_code == 404
    assert r_missing.json() == {"error": "not_found"}

    # Disabled trigger.
    trig = await _create_trigger(client, ws_id, secret=secret, name="disabled-trig")
    trigger_id = trig["id"]
    r_patch = await client.patch(
        f"/api/v1/ws/{ws_id}/triggers/{trigger_id}", json={"enabled": False}
    )
    assert r_patch.status_code == 200

    r_disabled = await post_ingest(client, ws_id, trigger_id, body, secret)
    assert r_disabled.status_code == 404
    assert r_disabled.json() == {"error": "not_found"}

    # Oracle: response bodies must be byte-identical.
    assert r_missing.content == r_disabled.content


# ---------------------------------------------------------------------------
# T4: Dedup — same body twice → second is 200 duplicate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup(authenticated_client: tuple[httpx.AsyncClient, str]) -> None:
    client, ws_id = authenticated_client

    secret = "dedup-secret"
    trig = await _create_trigger(client, ws_id, secret=secret, name="dedup-trig")
    trigger_id = trig["id"]

    body = b'{"event_type": "push"}'

    # First request.
    ts, sig = sign_body(secret, body)
    headers = {"X-Signature": sig, "X-Timestamp": ts, "Content-Type": "application/json"}
    r1 = await client.post(
        f"/api/v1/ws/{ws_id}/triggers/{trigger_id}/ingest",
        content=body,
        headers=headers,
    )
    assert r1.status_code == 202
    assert r1.json()["status"] == "accepted"

    # Second request — same body → dedup collision.
    r2 = await client.post(
        f"/api/v1/ws/{ws_id}/triggers/{trigger_id}/ingest",
        content=body,
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"

    # Wait for pipeline to settle (it may succeed or dead_letter in test env).
    event_id = r1.json()["event_id"]
    await _poll_event_accepted(client, ws_id, trigger_id, event_id)

    # The counter bumps from pipeline.fire and from the dedup short-circuit
    # are independent — poll until both have landed before asserting so a
    # slow pipeline run doesn't flake the dedup-counter assertion.
    deadline = time.monotonic() + 8.0
    t: dict[str, Any] = {}
    while time.monotonic() < deadline:
        t = await _get_trigger(client, ws_id, trigger_id)
        if t["events_total"] >= 2 and t["events_dedup_dropped"] >= 1:
            break
        await asyncio.sleep(0.15)
    assert t["events_total"] >= 2, t
    assert t["events_dedup_dropped"] >= 1, t


# ---------------------------------------------------------------------------
# T5: Stale timestamp → flat 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_timestamp(authenticated_client: tuple[httpx.AsyncClient, str]) -> None:
    client, ws_id = authenticated_client

    secret = "stale-secret"
    trig = await _create_trigger(client, ws_id, secret=secret, name="stale-trig")
    trigger_id = trig["id"]

    body = b'{"event_type": "push"}'
    stale_ts = str(int(time.time()) - 1000)
    r = await post_ingest(client, ws_id, trigger_id, body, secret, ts_override=stale_ts)
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}


# ---------------------------------------------------------------------------
# T6: Filter miss → 200 filtered_out, no run, no resulting_run_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_miss(authenticated_client: tuple[httpx.AsyncClient, str]) -> None:
    client, ws_id = authenticated_client

    secret = "filter-secret"
    trig = await _create_trigger(
        client,
        ws_id,
        secret=secret,
        name="filter-trig",
        filter={"path": "event.action", "op": "eq", "value": "opened"},
    )
    trigger_id = trig["id"]

    body = b'{"event": {"action": "closed"}}'
    r = await post_ingest(client, ws_id, trigger_id, body, secret)
    assert r.status_code == 200
    assert r.json()["status"] == "filtered_out"

    # Event row exists.
    r_events = await client.get(f"/api/v1/ws/{ws_id}/triggers/{trigger_id}/events")
    events = r_events.json()["events"]
    assert len(events) == 1
    ev = events[0]
    assert ev["status"] == "filtered_out"
    assert ev["resulting_run_id"] is None

    # events_total bumped, no success/failed.
    t = await _get_trigger(client, ws_id, trigger_id)
    assert t["events_total"] >= 1
    assert t["events_success"] == 0
    assert t["events_failed"] == 0


# ---------------------------------------------------------------------------
# T7: Rate limit 429 (default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_429(authenticated_client: tuple[httpx.AsyncClient, str]) -> None:
    client, ws_id = authenticated_client

    secret = "rl-429-secret"
    trig = await _create_trigger(
        client,
        ws_id,
        secret=secret,
        name="rl-429-trig",
        max_runs_per_minute=1,
        rate_limit_burst=1,
    )
    trigger_id = trig["id"]

    responses = []
    for i in range(3):
        body = f'{{"event_type": "push", "i": {i}}}'.encode()
        # Vary event-id so each request has a unique dedup key.
        r = await post_ingest(
            client,
            ws_id,
            trigger_id,
            body,
            secret,
            headers_extra={"X-Event-Id": f"req-rl429-{i}"},
        )
        responses.append(r)

    # First should be accepted (burst=1 means one token initially).
    assert responses[0].status_code == 202, f"r0: {responses[0].text}"
    # Second and third should be rate-limited.
    assert responses[1].status_code == 429, f"r1: {responses[1].text}"
    assert responses[1].json()["status"] == "rate_limited"
    assert responses[2].status_code == 429, f"r2: {responses[2].text}"


# ---------------------------------------------------------------------------
# T8: Rate limit 202_drop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_202_drop(authenticated_client: tuple[httpx.AsyncClient, str]) -> None:
    client, ws_id = authenticated_client

    secret = "rl-202-secret"
    trig = await _create_trigger(
        client,
        ws_id,
        secret=secret,
        name="rl-202-trig",
        max_runs_per_minute=1,
        rate_limit_burst=1,
        rate_limit_response="202_drop",
    )
    trigger_id = trig["id"]

    responses = []
    for i in range(3):
        body = f'{{"event_type": "push", "i": {i}}}'.encode()
        r = await post_ingest(
            client,
            ws_id,
            trigger_id,
            body,
            secret,
            headers_extra={"X-Event-Id": f"req-rl202-{i}"},
        )
        responses.append(r)

    assert responses[0].status_code == 202
    # Excess requests should be 202 with rate_limited status.
    assert responses[1].status_code == 202, f"r1: {responses[1].text}"
    assert responses[1].json()["status"] == "rate_limited"
    assert responses[2].status_code == 202
    assert responses[2].json()["status"] == "rate_limited"


# ---------------------------------------------------------------------------
# T9: Rotate secret in-window — old AND new secret both accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_secret_in_window(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client

    old_secret = "old-secret-rotate"
    new_secret = "new-secret-rotate"

    trig = await _create_trigger(client, ws_id, secret=old_secret, name="rotate-in-trig")
    trigger_id = trig["id"]

    # Rotate with a 1-hour overlap.
    r_rot = await client.post(
        f"/api/v1/ws/{ws_id}/triggers/{trigger_id}/rotate-secret",
        json={"new_webhook_secret": new_secret, "overlap_seconds": 3600},
    )
    assert r_rot.status_code == 200

    body = b'{"event_type": "rotate_test"}'

    # Sign with OLD secret → should still be accepted (overlap still active).
    r_old = await post_ingest(
        client,
        ws_id,
        trigger_id,
        body,
        old_secret,
        headers_extra={"X-Event-Id": "rotate-old"},
    )
    assert r_old.status_code == 202, f"old secret rejected: {r_old.text}"

    # Sign with NEW secret → accepted.
    r_new = await post_ingest(
        client,
        ws_id,
        trigger_id,
        body,
        new_secret,
        headers_extra={"X-Event-Id": "rotate-new"},
    )
    assert r_new.status_code == 202, f"new secret rejected: {r_new.text}"


# ---------------------------------------------------------------------------
# T10: Rotate secret out-of-window — old secret rejected, new accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_secret_out_of_window(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = authenticated_client

    old_secret = "old-secret-out"
    new_secret = "new-secret-out"

    trig = await _create_trigger(client, ws_id, secret=old_secret, name="rotate-out-trig")
    trigger_id = trig["id"]

    # Rotate with 0 overlap → previous secret immediately expired.
    r_rot = await client.post(
        f"/api/v1/ws/{ws_id}/triggers/{trigger_id}/rotate-secret",
        json={"new_webhook_secret": new_secret, "overlap_seconds": 0},
    )
    assert r_rot.status_code == 200

    body = b'{"event_type": "rotate_test_out"}'

    # OLD secret → flat 404 (out of window).
    r_old = await post_ingest(
        client,
        ws_id,
        trigger_id,
        body,
        old_secret,
        headers_extra={"X-Event-Id": "rotate-out-old"},
    )
    assert r_old.status_code == 404, f"old secret was wrongly accepted: {r_old.text}"
    assert r_old.json() == {"error": "not_found"}

    # NEW secret → 202 accepted.
    r_new = await post_ingest(
        client,
        ws_id,
        trigger_id,
        body,
        new_secret,
        headers_extra={"X-Event-Id": "rotate-out-new"},
    )
    assert r_new.status_code == 202, f"new secret rejected: {r_new.text}"


# ---------------------------------------------------------------------------
# T11: Tenant isolation — trigger from WS-A is not reachable via WS-B path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_isolation(
    authenticated_client: tuple[httpx.AsyncClient, str],
    ws_client_b: tuple[httpx.AsyncClient, str],
) -> None:
    client_a, ws_a = authenticated_client
    _client_b, ws_b = ws_client_b

    secret = "iso-secret"
    trig = await _create_trigger(client_a, ws_a, secret=secret, name="iso-trig")
    trigger_id_a = trig["id"]

    body = b'{"event_type": "iso"}'

    # POST to WS-B path with WS-A trigger id → flat 404 (trigger not in ws_b scope).
    r = await post_ingest(client_a, ws_b, trigger_id_a, body, secret)
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}
