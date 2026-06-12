"""E2E tests for the Feishu webhook ingress route (Task 12)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubebox.credentials.dependencies import build_credential_service
from cubebox.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
)
from tests.e2e.conftest import _build_database_url

pytestmark = pytest.mark.asyncio


_ORG_ID = "org-imingA"
_WS_ID = "ws-imingA"
_USER_ID = "usr-imingA"
_APP_ID = "cli_ingA"
_BOT_OPEN_ID = "ou_ingA_bot"
_VERIFICATION_TOKEN = "vt-ingA-from-dashboard"
_ENCRYPT_KEY = "ek-ingA-32-chars-aaaaaaaaaaaaaaaaaa"


def _sign(*, ts: str, nonce: str, body: bytes) -> str:
    return hashlib.sha256(f"{ts}{nonce}{_ENCRYPT_KEY}{body.decode()}".encode()).hexdigest()


@pytest_asyncio.fixture
async def _seeded_feishu_account(
    async_client: httpx.AsyncClient,
) -> AsyncIterator[None]:
    """Seed an org/ws/user via raw SQL + an im_bot Credential (real encryption)
    via the running app's encryption backend, then an IMConnectorAccount that
    references the credential. The async_client lifespan owns the app and its
    encryption backend.
    """
    # 1) Reach into the live app's encryption backend through CredentialService.
    transport = getattr(async_client, "_transport", None)
    asgi_transport: Any = transport
    if asgi_transport is None or not hasattr(asgi_transport, "app"):
        raise RuntimeError("async_client transport missing app")
    app = asgi_transport.app
    backend = app.state.encryption_backend

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        # Seed the FK targets.
        async with maker() as session:
            await session.execute(
                text(
                    "INSERT INTO organizations (id, name, slug, created_at)"
                    " VALUES (:id, :id, :id, NOW()) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _ORG_ID},
            )
            await session.execute(
                text(
                    "INSERT INTO workspaces (id, org_id, name, created_at)"
                    " VALUES (:id, :org, :id, NOW()) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _WS_ID, "org": _ORG_ID},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, is_active,"
                    " is_superuser, is_verified, created_at, language)"
                    " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _USER_ID, "email": f"{_USER_ID}@example.com"},
            )
            await session.commit()

        # 2) Create the credential via the real service (so the bytes are
        #    encrypted with the same key the ingress route will use to decrypt).
        secret_payload = {
            "app_id": _APP_ID,
            "app_secret": "secret",
            "encrypt_key": _ENCRYPT_KEY,
            "verification_token": _VERIFICATION_TOKEN,
            "domain": "feishu",
            "bot_open_id": _BOT_OPEN_ID,
        }
        async with maker() as session:
            svc = build_credential_service(session, backend, org_id=_ORG_ID, actor_user_id=_USER_ID)
            cred_id = await svc.create(
                kind="im_bot",
                name=f"feishu:{_APP_ID}",
                plaintext=json.dumps(secret_payload),
            )
            await session.commit()

        # 3) Insert the IMConnectorAccount referencing it.
        async with maker() as session:
            await session.execute(
                text(
                    "INSERT INTO im_connector_accounts (id, org_id, workspace_id,"
                    " platform, external_account_id, acting_user_id, credential_id,"
                    " delivery_mode, enabled, config, created_at, updated_at)"
                    " VALUES (:id, :org, :ws, 'feishu', :ext, :uid, :cred,"
                    " 'webhook', true, '{}'::jsonb, NOW(), NOW())"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": f"imac-ingA-{cred_id[:8]}",
                    "org": _ORG_ID,
                    "ws": _WS_ID,
                    "ext": _APP_ID,
                    "uid": _USER_ID,
                    "cred": cred_id,
                },
            )
            await session.commit()

        try:
            yield None
        finally:
            async with maker() as session:
                await session.execute(
                    text(
                        "DELETE FROM im_run_queue WHERE account_id IN"
                        " (SELECT id FROM im_connector_accounts WHERE external_account_id = :ext)"
                    ),
                    {"ext": _APP_ID},
                )
                await session.execute(
                    text(
                        "DELETE FROM im_webhook_receipts WHERE account_id IN"
                        " (SELECT id FROM im_connector_accounts WHERE external_account_id = :ext)"
                    ),
                    {"ext": _APP_ID},
                )
                await session.execute(
                    text(
                        "DELETE FROM im_thread_links WHERE account_id IN"
                        " (SELECT id FROM im_connector_accounts WHERE external_account_id = :ext)"
                    ),
                    {"ext": _APP_ID},
                )
                await session.execute(
                    text("DELETE FROM im_connector_accounts WHERE external_account_id = :ext"),
                    {"ext": _APP_ID},
                )
                await session.execute(
                    text("DELETE FROM credentials WHERE id = :id"), {"id": cred_id}
                )
                await session.execute(
                    text("DELETE FROM conversations WHERE workspace_id = :id"),
                    {"id": _WS_ID},
                )
                await session.commit()
    finally:
        await engine.dispose()


def _ev_callback_body(*, event_id: str = "ev_iA1", text_: str = "hello") -> bytes:
    return json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": event_id,
                "event_type": "im.message.receive_v1",
                "token": _VERIFICATION_TOKEN,
                "app_id": _APP_ID,
            },
            "event": {
                "sender": {
                    "sender_id": {"open_id": "ou_user1", "union_id": "on_user1"},
                    "sender_type": "user",
                },
                "message": {
                    "message_id": "om_iA1",
                    "chat_id": "oc_iA_dm",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": json.dumps({"text": text_}),
                },
            },
        }
    ).encode()


async def test_event_callback_enqueues_run(
    async_client: httpx.AsyncClient,
    _seeded_feishu_account: None,
) -> None:
    body = _ev_callback_body()
    ts = "1700000000"
    nonce = "abc"
    headers = {
        "x-lark-request-timestamp": ts,
        "x-lark-request-nonce": nonce,
        "x-lark-signature": _sign(ts=ts, nonce=nonce, body=body),
        "Content-Type": "application/json",
    }
    resp = await async_client.post("/api/v1/im/feishu/events", content=body, headers=headers)
    assert resp.status_code == 200, resp.text

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            account = (
                await session.execute(
                    select(IMConnectorAccount).where(
                        IMConnectorAccount.external_account_id == _APP_ID  # type: ignore[arg-type]
                    )
                )
            ).scalar_one()
            items = (
                (
                    await session.execute(
                        select(IMRunQueueItem).where(
                            IMRunQueueItem.account_id == account.id  # type: ignore[arg-type]
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(items) == 1
            assert items[0].scope_key == "dm"
            assert items[0].channel_id == "oc_iA_dm"
            assert items[0].content == "hello"
    finally:
        await engine.dispose()


async def test_url_verification_challenge_returned(
    async_client: httpx.AsyncClient,
    _seeded_feishu_account: None,
) -> None:
    body = json.dumps(
        {
            "type": "url_verification",
            "challenge": "abc123",
            "token": _VERIFICATION_TOKEN,
            "app_id": _APP_ID,
        }
    ).encode()
    # url_verification doesn't carry x-lark-* signature headers; the
    # verification_token IS the check.
    resp = await async_client.post(
        "/api/v1/im/feishu/events",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"challenge": "abc123"}


async def test_bad_verification_token_rejected(
    async_client: httpx.AsyncClient,
    _seeded_feishu_account: None,
) -> None:
    body = json.dumps(
        {
            "type": "url_verification",
            "challenge": "abc123",
            "token": "WRONG",
            "app_id": _APP_ID,
        }
    ).encode()
    resp = await async_client.post(
        "/api/v1/im/feishu/events",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


async def test_bad_signature_rejected(
    async_client: httpx.AsyncClient,
    _seeded_feishu_account: None,
) -> None:
    body = _ev_callback_body(event_id="ev_iA_bad")
    headers = {
        "x-lark-request-timestamp": "1700000000",
        "x-lark-request-nonce": "abc",
        "x-lark-signature": "deadbeef",
        "Content-Type": "application/json",
    }
    resp = await async_client.post("/api/v1/im/feishu/events", content=body, headers=headers)
    assert resp.status_code == 401


async def test_unknown_app_acked_and_dropped(
    async_client: httpx.AsyncClient,
) -> None:
    body = json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": "ev_unknown",
                "event_type": "im.message.receive_v1",
                "token": "anything",
                "app_id": "cli_UNKNOWN",
            },
            "event": {},
        }
    ).encode()
    resp = await async_client.post(
        "/api/v1/im/feishu/events", content=body, headers={"Content-Type": "application/json"}
    )
    # Ack without disclosing existence; the platform retries are gracefully absorbed.
    assert resp.status_code == 200


def _encrypt_body_for_test(plaintext: bytes, encrypt_key: str) -> str:
    """Mirror of decrypt_feishu_payload to fabricate encrypted webhook bodies."""
    import base64
    import hashlib
    import secrets as _secrets

    from Crypto.Cipher import AES

    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = _secrets.token_bytes(16)
    pad = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad]) * pad
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(padded)
    return base64.b64encode(iv + ct).decode()


async def test_encrypted_event_callback_decrypts_and_enqueues(
    async_client: httpx.AsyncClient,
    _seeded_feishu_account: None,
) -> None:
    """Feishu "Event Encryption" mode: outer body has only {"encrypt": "..."};
    ingress must try-decrypt against each enabled account's encrypt_key,
    route by the inner app_id, and enqueue a run just like the plain path.
    """
    inner = _ev_callback_body(event_id="ev_iA_enc")
    encrypted = _encrypt_body_for_test(inner, _ENCRYPT_KEY)
    body = json.dumps({"encrypt": encrypted}).encode()
    # Encryption mode: Feishu still computes signature over the OUTER body.
    ts, nonce = "1700000000", "abc"
    headers = {
        "x-lark-request-timestamp": ts,
        "x-lark-request-nonce": nonce,
        "x-lark-signature": _sign(ts=ts, nonce=nonce, body=body),
        "Content-Type": "application/json",
    }
    resp = await async_client.post("/api/v1/im/feishu/events", content=body, headers=headers)
    assert resp.status_code == 200, resp.text


async def test_encrypted_payload_with_non_string_encrypt_rejected(
    async_client: httpx.AsyncClient,
    _seeded_feishu_account: None,
) -> None:
    """A malformed ``encrypt`` field (not a base64 string) is a programming
    error or attack probe — refuse loudly with 400 rather than silently
    drop, so misconfiguration is visible."""
    body = json.dumps({"encrypt": {"unexpected": "object"}}).encode()
    resp = await async_client.post(
        "/api/v1/im/feishu/events", content=body, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400


async def test_encrypted_payload_unknown_account_acked(
    async_client: httpx.AsyncClient,
    _seeded_feishu_account: None,
) -> None:
    """If no enabled account's encrypt_key decrypts the body, return 200 +
    log (don't 4xx — Feishu would otherwise mark the endpoint unhealthy)."""
    body = json.dumps({"encrypt": "not-valid-base64-ciphertext"}).encode()
    resp = await async_client.post(
        "/api/v1/im/feishu/events", content=body, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 200
