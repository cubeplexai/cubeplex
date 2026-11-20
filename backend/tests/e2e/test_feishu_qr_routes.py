"""E2E tests for Feishu/Lark QR-code onboarding routes."""

from __future__ import annotations

import asyncio
import secrets as _secrets
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from cubeplex.im.feishu.qr_registration import FeishuQrRegistrationManager

pytestmark = pytest.mark.asyncio


def _unique_app_id(tag: str) -> str:
    return f"cli_{tag}_{_secrets.token_hex(4)}"


@patch("cubeplex.services.im_connector.IMConnectorService._hydrate_bot_info")
async def test_feishu_qr_start_poll_and_connect(
    mock_hydrate: Any,
    async_client: httpx.AsyncClient,
) -> None:
    """Starting a Feishu QR flow returns a QR URL and connects the bot upon approval."""
    app_id = _unique_app_id("qr")
    proceed = asyncio.Event()

    async def _fake_hydrate(aid: str, secret: str, domain: str) -> tuple[str, str, str]:
        return "ou_qr_bot", "QR Bot", ""

    mock_hydrate.side_effect = _fake_hydrate

    async def _fake_register_app(**kwargs: Any) -> dict[str, Any]:
        kwargs["on_qr_code"](
            {"url": "https://open.feishu.cn/page/launcher?tp=sdk&mock=1", "expire_in": 300}
        )
        await proceed.wait()
        return {
            "client_id": app_id,
            "client_secret": "qr_super_secret",
            "user_info": {"open_id": "ou_scanner", "tenant_brand": "feishu"},
        }

    fastapi_app: Any = async_client._transport.app
    prev_mgr = getattr(fastapi_app.state, "feishu_qr_manager", None)
    prev_connect = getattr(fastapi_app.state, "im_connect_account", None)
    fastapi_app.state.feishu_qr_manager = FeishuQrRegistrationManager(
        register_app=_fake_register_app
    )
    fastapi_app.state.im_connect_account = AsyncMock()

    try:
        from tests.e2e.conftest import DEFAULT_WS_ID

        started = await async_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/im/feishu/qr/start",
            json={"domain": "feishu"},
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["state"] == "qr_ready"
        assert body["qr_url"] == "https://open.feishu.cn/page/launcher?tp=sdk&mock=1"
        assert "qr_super_secret" not in started.text

        attempt_id = body["attempt_id"]
        proceed.set()

        final: dict[str, Any] = {}
        for _ in range(30):
            await asyncio.sleep(0.02)
            polled = await async_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/im/feishu/qr/{attempt_id}")
            assert polled.status_code == 200, polled.text
            final = polled.json()
            if final["state"] == "succeeded":
                break
        else:
            pytest.fail(f"QR attempt did not succeed: {final}")

        assert final["account"]["external_account_id"] == app_id
        assert final["account"]["delivery_mode"] == "long_connection"
        assert "qr_super_secret" not in str(final)

        listed = await async_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/im/accounts")
        assert listed.status_code == 200
        assert any(a["external_account_id"] == app_id for a in listed.json()["accounts"])
    finally:
        fastapi_app.state.feishu_qr_manager = prev_mgr
        fastapi_app.state.im_connect_account = prev_connect


async def test_feishu_qr_cancel(
    async_client: httpx.AsyncClient,
) -> None:
    """Cancelling an active Feishu QR attempt transitions it to cancelled."""
    gate = asyncio.Event()

    async def _fake_register_app(**kwargs: Any) -> dict[str, Any]:
        kwargs["on_qr_code"](
            {"url": "https://open.feishu.cn/page/launcher?tp=sdk", "expire_in": 300}
        )
        await gate.wait()
        return {"client_id": "cli_never", "client_secret": "sec_never"}

    fastapi_app: Any = async_client._transport.app
    prev_mgr = getattr(fastapi_app.state, "feishu_qr_manager", None)
    fastapi_app.state.feishu_qr_manager = FeishuQrRegistrationManager(
        register_app=_fake_register_app
    )

    try:
        from tests.e2e.conftest import DEFAULT_WS_ID

        started = await async_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/im/feishu/qr/start",
            json={"domain": "lark"},
        )
        assert started.status_code == 200
        attempt_id = started.json()["attempt_id"]

        cancelled = await async_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/im/feishu/qr/{attempt_id}/cancel"
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
        assert cancelled.json()["qr_url"] is None
    finally:
        fastapi_app.state.feishu_qr_manager = prev_mgr
