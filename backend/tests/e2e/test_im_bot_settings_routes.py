"""E2E for the account-level IM bot settings routes (PR2).

Covers the GET/PUT contract on
``/api/v1/ws/{ws}/im/accounts/{id}/settings``: defaults, round-trip
persistence, shared-mode validation, workspace isolation, and the admin gate
on PUT.
"""

from __future__ import annotations

import secrets as _secrets
from typing import Any
from unittest.mock import patch

import httpx
import pytest

pytestmark = pytest.mark.asyncio


def _app_id(tag: str) -> str:
    return f"cli_{tag}_{_secrets.token_hex(4)}"


async def _create_account(client: httpx.AsyncClient, ws_id: str, tag: str) -> str:
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/im/accounts",
        json={
            "platform": "feishu",
            "app_id": _app_id(tag),
            "app_secret": "secret",
            "encrypt_key": "ek",
            "verification_token": "vt",
            "domain": "feishu",
            "delivery_mode": "long_connection",
            "acting_user_id": "self",
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


@patch("cubebox.services.im_connector.IMConnectorService._hydrate_bot_info")
async def test_get_defaults_then_put_roundtrip(
    mock_hydrate: Any,
    async_client: httpx.AsyncClient,
) -> None:
    """Defaults on first read; PUT persists and is reflected by GET."""

    async def _fake(app_id: str, app_secret: str, domain: str) -> tuple[str, str, str]:
        return "ou_bot", "", ""

    mock_hydrate.side_effect = _fake
    from tests.e2e.conftest import DEFAULT_WS_ID

    account_id = await _create_account(async_client, DEFAULT_WS_ID, "settings")
    base = f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account_id}/settings"
    try:
        # Unset → defaults.
        got = await async_client.get(base)
        assert got.status_code == 200, got.text
        assert got.json() == {
            "routing_mode": "isolated",
            "topic_mode": "topic",
            "sandbox_mode": None,
        }

        # Update to shared + sandbox; response echoes the stored settings.
        put = await async_client.put(
            base,
            json={
                "routing_mode": "shared",
                "topic_mode": "flat",
                "sandbox_mode": "dedicated",
            },
        )
        assert put.status_code == 200, put.text
        assert put.json() == {
            "routing_mode": "shared",
            "topic_mode": "flat",
            "sandbox_mode": "dedicated",
        }

        # Persisted across a fresh read.
        got2 = await async_client.get(base)
        assert got2.json()["routing_mode"] == "shared"
        assert got2.json()["sandbox_mode"] == "dedicated"
    finally:
        await async_client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account_id}")


@patch("cubebox.services.im_connector.IMConnectorService._hydrate_bot_info")
async def test_put_shared_requires_sandbox_mode(
    mock_hydrate: Any,
    async_client: httpx.AsyncClient,
) -> None:
    """shared routing without a sandbox_mode is rejected (422)."""

    async def _fake(app_id: str, app_secret: str, domain: str) -> tuple[str, str, str]:
        return "ou_bot", "", ""

    mock_hydrate.side_effect = _fake
    from tests.e2e.conftest import DEFAULT_WS_ID

    account_id = await _create_account(async_client, DEFAULT_WS_ID, "sandbox")
    base = f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account_id}/settings"
    try:
        resp = await async_client.put(
            base, json={"routing_mode": "shared", "topic_mode": "topic"}
        )
        assert resp.status_code == 422, resp.text
        # A non-enum sandbox value is also rejected (only dedicated|creator).
        bad = await async_client.put(
            base,
            json={"routing_mode": "shared", "topic_mode": "topic", "sandbox_mode": "nope"},
        )
        assert bad.status_code == 422, bad.text
        # And the account's settings stay at the defaults (no partial write).
        assert (await async_client.get(base)).json()["routing_mode"] == "isolated"
    finally:
        await async_client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account_id}")


@patch("cubebox.services.im_connector.IMConnectorService._hydrate_bot_info")
async def test_put_rejects_shared_for_teams(
    mock_hydrate: Any,
    async_client: httpx.AsyncClient,
) -> None:
    """Teams can't do channel-shared scope, so shared routing is rejected on
    the API too (not just disabled in the UI)."""

    async def _fake(app_id: str, app_secret: str, domain: str) -> tuple[str, str, str]:
        return "ou_bot", "", ""

    mock_hydrate.side_effect = _fake
    from cubebox.db.engine import async_session_maker
    from cubebox.models.im_connector import IMConnectorAccount
    from tests.e2e.conftest import DEFAULT_WS_ID

    account_id = await _create_account(async_client, DEFAULT_WS_ID, "teams")
    base = f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account_id}/settings"
    try:
        async with async_session_maker() as s:
            acct = await s.get(IMConnectorAccount, account_id)
            assert acct is not None
            acct.platform = "teams"
            await s.commit()
        resp = await async_client.put(
            base,
            json={"routing_mode": "shared", "topic_mode": "topic", "sandbox_mode": "dedicated"},
        )
        assert resp.status_code == 422, resp.text
        # Isolated still works for Teams.
        ok = await async_client.put(
            base, json={"routing_mode": "isolated", "topic_mode": "topic"}
        )
        assert ok.status_code == 200, ok.text
    finally:
        await async_client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account_id}")


async def test_get_unknown_account_404(async_client: httpx.AsyncClient) -> None:
    from tests.e2e.conftest import DEFAULT_WS_ID

    resp = await async_client.get(
        f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/imac-does-not-exist/settings"
    )
    assert resp.status_code == 404


async def test_put_foreign_workspace_404(async_client: httpx.AsyncClient) -> None:
    """A workspace the caller isn't a member of is rejected by require_member
    with a 404 (scope isolation: don't leak that the workspace exists)."""
    resp = await async_client.put(
        "/api/v1/ws/ws-not-mine/im/accounts/imac-x/settings",
        json={"routing_mode": "isolated", "topic_mode": "topic"},
    )
    assert resp.status_code == 404


@patch("cubebox.services.im_connector.IMConnectorService._hydrate_bot_info")
async def test_put_requires_admin(
    mock_hydrate: Any,
    async_client: httpx.AsyncClient,
    ws_member_client: Any,
) -> None:
    """A plain workspace member cannot change bot settings (admin-only PUT);
    GET is allowed for members."""

    async def _fake(app_id: str, app_secret: str, domain: str) -> tuple[str, str, str]:
        return "ou_bot", "", ""

    mock_hydrate.side_effect = _fake
    from tests.e2e.conftest import DEFAULT_WS_ID

    account_id = await _create_account(async_client, DEFAULT_WS_ID, "rbac")
    base = f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account_id}/settings"
    try:
        # Member CAN read.
        assert ws_member_client.get(base).status_code == 200
        # Member CANNOT write (valid body, so we hit the admin gate, not 422).
        put = ws_member_client.put(
            base,
            json={"routing_mode": "shared", "topic_mode": "topic", "sandbox_mode": "dedicated"},
        )
        assert put.status_code == 403, put.text
    finally:
        await async_client.delete(f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts/{account_id}")
