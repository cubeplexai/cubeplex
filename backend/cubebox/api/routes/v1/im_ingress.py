"""Platform-signed IM ingress.

Unauthenticated by cubebox session — the verification is the platform's
signature/token. Order of operations matters: verification_token check
runs BEFORE we echo a ``url_verification`` challenge so an attacker cannot
prove endpoint control by getting their supplied challenge bounced back.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.credentials.dependencies import (
    build_credential_service,
    get_encryption_backend,
)
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.engine import async_session_maker
from cubebox.db.session import get_session
from cubebox.im.feishu.connector import FeishuConnector
from cubebox.im.feishu.signature import (
    FeishuSignatureError,
    verify_feishu_signature,
    verify_verification_token,
)
from cubebox.im.inbound import ingest_inbound_event
from cubebox.repositories.im_connector import get_account_by_external_id_unscoped

router = APIRouter(prefix="/im", tags=["im-ingress"])


@router.post("/feishu/events")
async def feishu_events(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> Response:
    """Receive one Feishu webhook event.

    Returns 200 on every accepted-or-ignored event (Feishu retries on
    non-200) and explicit error codes only for genuine verification
    failures. Unknown accounts ack and drop — never error-leak.
    """
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)
    if not isinstance(payload, dict):
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    # Some Feishu v1 events stash the verification token at the top level;
    # v2 events put it under header.token. Accept either source.
    header = payload.get("header") or {}
    incoming_token = str(header.get("token") or payload.get("token") or "")

    # Resolve the account first so we know which encrypt key / token to check.
    # For url_verification the payload identifies the app via top-level
    # app_id; for event_callback / v2 it's header.app_id.
    external_id = str(header.get("app_id") or payload.get("app_id") or "")
    account = await get_account_by_external_id_unscoped(
        session, platform="feishu", external_account_id=external_id
    )
    if account is None or not account.enabled:
        # Ack + drop — never disclose "we don't know this app".
        return Response(status_code=status.HTTP_200_OK)

    cred_service = build_credential_service(
        session,
        backend,
        org_id=account.org_id,
        actor_user_id=None,
    )
    try:
        secret_json = await cred_service.get_decrypted(
            credential_id=account.credential_id, requesting_kind="im_bot"
        )
    except Exception:
        logger.exception("[Feishu ingress] credential decryption failed")
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    secrets = json.loads(secret_json)

    # Verification token first — same call shape Feishu used in both v1 and v2.
    try:
        verify_verification_token(
            expected=str(secrets.get("verification_token") or ""),
            incoming=incoming_token,
        )
    except FeishuSignatureError as exc:
        logger.warning("[Feishu ingress] verification token rejected: {}", exc)
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    # url_verification challenge — only echo AFTER the token check passes,
    # so we never bounce attacker-supplied challenge data without auth.
    if payload.get("type") == "url_verification":
        return Response(
            content=json.dumps({"challenge": payload.get("challenge", "")}),
            media_type="application/json",
        )

    # Encrypted-payload guard — v1 cubebox does not support encrypt-mode
    # webhook bodies (they require a separate decrypt step).
    if payload.get("encrypt"):
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    # Signature verification (skipped when no encrypt_key configured — Feishu
    # only sends the x-lark-signature header for encrypt-enabled apps; the
    # verification_token above is the standalone safeguard for plain mode).
    encrypt_key = str(secrets.get("encrypt_key") or "")
    if encrypt_key:
        try:
            verify_feishu_signature(
                encrypt_key=encrypt_key,
                raw_body=raw_body,
                timestamp=request.headers.get("x-lark-request-timestamp", ""),
                nonce=request.headers.get("x-lark-request-nonce", ""),
                signature=request.headers.get("x-lark-signature", ""),
            )
        except FeishuSignatureError as exc:
            logger.warning("[Feishu ingress] signature rejected: {}", exc)
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    # Parse + ingest the message event. bot_open_id was hydrated at
    # connect_feishu time (Task 15) and lives on the credential.
    bot_open_id = str(secrets.get("bot_open_id") or "") or None
    connector = FeishuConnector(bot_open_id=bot_open_id)
    event = connector.parse_inbound(payload)
    if event is None:
        # Not a message we act on (bot echo, non-text, non-mention in group, ...).
        return Response(status_code=status.HTTP_200_OK)

    # The webhook payload doesn't natively carry the account's external id
    # in the place we want — fill it from the account we just resolved.
    event.account_external_id = account.external_account_id

    # Use the module-level session maker so ingest_inbound_event owns its
    # own transaction (the request's session is bound to FastAPI's
    # dependency lifetime and isn't safe to share across transactions).
    maker: async_sessionmaker[AsyncSession] = async_session_maker
    result = await ingest_inbound_event(event, account=account, session_maker=maker)
    logger.info("[Feishu ingress] {} {}: {}", account.id, event.platform_event_id, result.outcome)
    return Response(status_code=status.HTTP_200_OK)
