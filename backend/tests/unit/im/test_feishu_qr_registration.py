"""Unit tests for Feishu/Lark QR-code one-click registration manager."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from lark_oapi.scene.registration.errors import AppAccessDeniedError, AppExpiredError

from cubeplex.api.schemas.im_connector import IMAccountOut, ImRuntimeStatus
from cubeplex.im.feishu.qr_registration import (
    FEISHU_QR_ADDONS,
    FeishuDomain,
    FeishuQrRegistrationManager,
)


def _dummy_account(app_id: str, domain: FeishuDomain) -> IMAccountOut:
    return IMAccountOut(
        id="im-1",
        platform="feishu",
        external_account_id=app_id,
        workspace_id="ws-1",
        acting_user_id="user-1",
        delivery_mode="long_connection",
        enabled=True,
        runtime=ImRuntimeStatus.unknown(),
        bot_app_name="CubePlex AI Assistant",
    )


@pytest.mark.asyncio
async def test_qr_registration_success_and_domain_switch() -> None:
    captured_kwargs: dict[str, Any] = {}
    proceed = asyncio.Event()

    async def fake_register_app(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        kwargs["on_qr_code"](
            {"url": "https://open.feishu.cn/page/launcher?tp=sdk", "expire_in": 300}
        )
        kwargs["on_status_change"]({"status": "polling"})
        await proceed.wait()
        kwargs["on_status_change"]({"status": "domain_switched"})
        return {
            "client_id": "cli_test_123",
            "client_secret": "sec_super_secret",
            "user_info": {"open_id": "ou_123", "tenant_brand": "lark"},
        }

    connected: list[tuple[str, str, FeishuDomain]] = []

    async def fake_connect(app_id: str, app_secret: str, domain: FeishuDomain) -> IMAccountOut:
        connected.append((app_id, app_secret, domain))
        return _dummy_account(app_id, domain)

    mgr = FeishuQrRegistrationManager(register_app=fake_register_app)
    snap = await mgr.start(
        org_id="org-1",
        workspace_id="ws-1",
        user_id="user-1",
        domain="feishu",
        connect_bot=fake_connect,
    )

    assert snap.state in ("qr_ready", "polling")
    assert snap.qr_url == "https://open.feishu.cn/page/launcher?tp=sdk"
    assert snap.expires_at is not None
    assert captured_kwargs["create_only"] is True
    assert captured_kwargs["addons"] == FEISHU_QR_ADDONS

    proceed.set()
    for _ in range(20):
        await asyncio.sleep(0.01)
        cur = mgr.get_status(
            snap.attempt_id,
            org_id="org-1",
            workspace_id="ws-1",
            user_id="user-1",
        )
        assert cur is not None
        if cur.state == "succeeded":
            break
    else:
        pytest.fail("attempt did not reach succeeded state")

    assert cur.domain == "lark"
    assert cur.qr_url is None
    assert cur.account is not None
    assert cur.account.external_account_id == "cli_test_123"
    assert connected == [("cli_test_123", "sec_super_secret", "lark")]


@pytest.mark.asyncio
async def test_qr_registration_scope_isolation_and_cancel() -> None:
    gate = asyncio.Event()

    async def fake_register_app(**kwargs: Any) -> dict[str, Any]:
        kwargs["on_qr_code"]({"url": "https://open.feishu.cn/qr", "expire_in": 300})
        await gate.wait()
        return {"client_id": "cli_x", "client_secret": "sec_x"}

    async def fake_connect(app_id: str, app_secret: str, domain: FeishuDomain) -> IMAccountOut:
        return _dummy_account(app_id, domain)

    mgr = FeishuQrRegistrationManager(register_app=fake_register_app)
    snap = await mgr.start(
        org_id="org-1",
        workspace_id="ws-1",
        user_id="user-1",
        connect_bot=fake_connect,
    )

    assert (
        mgr.get_status(snap.attempt_id, org_id="org-1", workspace_id="ws-1", user_id="user-2")
        is None
    )
    assert (
        mgr.get_status(snap.attempt_id, org_id="org-1", workspace_id="ws-2", user_id="user-1")
        is None
    )
    assert (
        mgr.cancel(snap.attempt_id, org_id="org-1", workspace_id="ws-1", user_id="user-2") is None
    )

    cancelled = mgr.cancel(snap.attempt_id, org_id="org-1", workspace_id="ws-1", user_id="user-1")
    assert cancelled is not None
    assert cancelled.state == "cancelled"
    assert cancelled.error_code == "abort"


@pytest.mark.asyncio
async def test_qr_registration_denied_and_expired() -> None:
    async def denied_register_app(**kwargs: Any) -> dict[str, Any]:
        kwargs["on_qr_code"]({"url": "https://open.feishu.cn/qr", "expire_in": 300})
        raise AppAccessDeniedError("access_denied", "user denied")

    async def expired_register_app(**kwargs: Any) -> dict[str, Any]:
        kwargs["on_qr_code"]({"url": "https://open.feishu.cn/qr", "expire_in": 300})
        raise AppExpiredError("expired_token", "timed out")

    async def fake_connect(app_id: str, app_secret: str, domain: FeishuDomain) -> IMAccountOut:
        return _dummy_account(app_id, domain)

    mgr_denied = FeishuQrRegistrationManager(register_app=denied_register_app)
    s1 = await mgr_denied.start(
        org_id="org-1", workspace_id="ws-1", user_id="user-1", connect_bot=fake_connect
    )
    await asyncio.sleep(0.02)
    res1 = mgr_denied.get_status(
        s1.attempt_id, org_id="org-1", workspace_id="ws-1", user_id="user-1"
    )
    assert res1 is not None
    assert res1.state == "error"
    assert res1.error_code == "access_denied"

    mgr_expired = FeishuQrRegistrationManager(register_app=expired_register_app)
    s2 = await mgr_expired.start(
        org_id="org-1", workspace_id="ws-1", user_id="user-1", connect_bot=fake_connect
    )
    await asyncio.sleep(0.02)
    res2 = mgr_expired.get_status(
        s2.attempt_id, org_id="org-1", workspace_id="ws-1", user_id="user-1"
    )
    assert res2 is not None
    assert res2.state == "expired"
    assert res2.error_code == "expired_token"
