"""Platform-signed IM ingress.

Unauthenticated by cubebox session — the verification is the platform's
signature/token. Order of operations matters: verification_token check
runs BEFORE we echo a ``url_verification`` challenge so an attacker cannot
prove endpoint control by getting their supplied challenge bounced back.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.credentials.dependencies import (
    build_credential_service,
    get_encryption_backend,
)
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.db.engine import async_session_maker
from cubebox.db.session import get_session
from cubebox.im.feishu.card_action_router import (
    InvalidAction,
    parse_action_payload,
)
from cubebox.im.feishu.card_action_router import (
    dispatch as dispatch_card_action,
)
from cubebox.im.feishu.connector import FeishuConnector
from cubebox.im.feishu.signature import (
    FeishuSignatureError,
    decrypt_feishu_payload,
    verify_feishu_signature,
    verify_verification_token,
)
from cubebox.im.inbound import ingest_inbound_event
from cubebox.im.resume import resume_paused_run
from cubebox.models.im_connector import IMConnectorAccount
from cubebox.repositories.im_connector import get_account_by_external_id_unscoped

router = APIRouter(prefix="/im", tags=["im-ingress"])


async def _try_decrypt_against_enabled_accounts(
    session: AsyncSession,
    backend: EncryptionBackend,
    encrypted_b64: str,
) -> tuple[dict[str, Any], dict[str, Any], IMConnectorAccount] | None:
    """Find the Feishu account whose encrypt_key successfully unwraps the body.

    Iterates enabled feishu accounts (typically 1–2 per cubebox deploy);
    returns ``(decrypted_payload, secrets, account)`` on first success or
    None if no account's key decrypts cleanly. O(N) per encrypted event,
    but N is small and the cost is amortized away by the secret cache on
    the long-connection side — the webhook path has no equivalent cache
    yet, so this is acceptable for v1.
    """
    enabled_accounts = (
        (
            await session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.platform == "feishu",  # type: ignore[arg-type]
                    IMConnectorAccount.enabled == True,  # type: ignore[arg-type]  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    for candidate in enabled_accounts:
        cred_service = build_credential_service(
            session,
            backend,
            org_id=candidate.org_id,
            actor_user_id=None,
        )
        try:
            secret_json = await cred_service.get_decrypted(
                credential_id=candidate.credential_id, requesting_kind="im_bot"
            )
        except Exception:
            logger.warning(
                "[Feishu ingress] credential decrypt failed for {} during encrypted-payload routing",
                candidate.id,
                exc_info=True,
            )
            continue
        candidate_secrets: dict[str, Any] = json.loads(secret_json)
        encrypt_key = str(candidate_secrets.get("encrypt_key") or "")
        if not encrypt_key:
            continue
        try:
            decrypted = decrypt_feishu_payload(encrypt_key=encrypt_key, encrypted_b64=encrypted_b64)
        except FeishuSignatureError:
            continue
        # Sanity check: the decrypted payload should mention this account's
        # app_id either at header.app_id (v2) or top-level (v1 / url_verification).
        d_header = decrypted.get("header") or {}
        decrypted_app_id = str(d_header.get("app_id") or decrypted.get("app_id") or "")
        if decrypted_app_id and decrypted_app_id != candidate.external_account_id:
            # Decrypted cleanly but app_id mismatches — another account's
            # encrypt_key happens to produce valid-looking padding. Skip.
            continue
        return decrypted, candidate_secrets, candidate
    return None


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

    # Encrypted-payload routing (Feishu "Event Encryption" toggle).
    # When enabled, the outer body is just ``{"encrypt": "<base64>"}`` —
    # the encrypted blob contains app_id but we can't see it from outside.
    # Try-decrypt against each enabled Feishu account; the one whose
    # encrypt_key unwraps to valid JSON wins. The chosen credential then
    # drives the rest of the flow (token + signature verification, ingest).
    # NB: Feishu sends the x-lark-signature over the OUTER body (the
    # ``{"encrypt": "..."}`` envelope), so ``raw_body`` stays untouched
    # for the signature check below.
    encrypted_field = payload.get("encrypt")
    if encrypted_field is not None:
        if not isinstance(encrypted_field, str):
            # Malformed payload — Feishu only ever sends `encrypt` as a
            # base64 string. Refuse loudly so attackers / buggy clients
            # don't get a silent ack.
            return Response(status_code=status.HTTP_400_BAD_REQUEST)
        decrypted = await _try_decrypt_against_enabled_accounts(session, backend, encrypted_field)
        if decrypted is None:
            logger.warning("[Feishu ingress] no enabled Feishu account could decrypt this payload")
            # 200 not 400 — Feishu would otherwise keep retrying and
            # mark the endpoint unhealthy on a misconfigured account.
            return Response(status_code=status.HTTP_200_OK)
        payload, secrets, account = decrypted
    else:
        # Some Feishu v1 events stash the verification token at the top level;
        # v2 events put it under header.token. Accept either source.
        header = payload.get("header") or {}
        # Resolve the account first so we know which encrypt key / token to check.
        # For url_verification the payload identifies the app via top-level
        # app_id; for event_callback / v2 it's header.app_id.
        external_id = str(header.get("app_id") or payload.get("app_id") or "")
        account_lookup = await get_account_by_external_id_unscoped(
            session, platform="feishu", external_account_id=external_id
        )
        if account_lookup is None or not account_lookup.enabled:
            # Ack + drop — never disclose "we don't know this app".
            return Response(status_code=status.HTTP_200_OK)
        account = account_lookup
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

    header = payload.get("header") or {}
    incoming_token = str(header.get("token") or payload.get("token") or "")

    # Early-out for accounts whose bot identity wasn't hydrated. Without
    # bot_open_id we cannot run the bot-echo guard, so the bot's own
    # outbound replies could be re-ingested as inbound and loop the agent
    # on itself. Drop BEFORE the verification-token / signature work so a
    # permanently-broken account doesn't burn HMAC cycles per inbound event.
    bot_open_id = str(secrets.get("bot_open_id") or "") or None
    if bot_open_id is None:
        logger.warning(
            "[Feishu ingress] dropping event — bot_open_id not hydrated on account {}",
            account.id,
        )
        return Response(status_code=status.HTTP_200_OK)

    # Verification token first — same call shape Feishu used in both v1 and v2.
    try:
        verify_verification_token(
            expected=str(secrets.get("verification_token") or ""),
            incoming=incoming_token,
        )
    except FeishuSignatureError as exc:
        logger.warning("[Feishu ingress] verification token rejected: {}", exc)
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    # CardKit button-click event branch — handled separately from
    # im.message.receive_v1 (no agent run kicked off, just a resume signal).
    event_type = str(header.get("event_type") or "")
    if event_type == "card.action.trigger":
        handled, toast = await _handle_card_action(payload)
        if handled:
            body: dict[str, Any] = {}
            if toast:
                body = {"toast": {"type": "info", "content": toast}}
            return Response(
                content=json.dumps(body),
                media_type="application/json",
            )

    # url_verification challenge — only echo AFTER the token check passes,
    # so we never bounce attacker-supplied challenge data without auth.
    if payload.get("type") == "url_verification":
        return Response(
            content=json.dumps({"challenge": payload.get("challenge", "")}),
            media_type="application/json",
        )

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

    # Parse + ingest the message event. ``bot_open_id`` was already
    # checked at the top of this handler (early-out path).
    connector = FeishuConnector(bot_open_id=bot_open_id)
    event = connector.parse_inbound(payload)
    if event is None:
        # Not a message we act on (bot echo, non-text, non-mention in group, ...).
        return Response(status_code=status.HTTP_200_OK)

    # The webhook payload doesn't natively carry the account's external id
    # in the place we want — fill it from the account we just resolved.
    event.account_external_id = account.external_account_id

    # Build a connector with a live lark Client for the identity gate
    # (email lookup + rejection reply). Same credentials as the bot's
    # webhook auth path; cheap to construct per request.
    gate_connector = _build_gate_connector(account, secrets, bot_open_id)

    # Use the module-level session maker so ingest_inbound_event owns its
    # own transaction (the request's session is bound to FastAPI's
    # dependency lifetime and isn't safe to share across transactions).
    maker: async_sessionmaker[AsyncSession] = async_session_maker
    result = await ingest_inbound_event(
        event,
        account=account,
        session_maker=maker,
        identity_resolver=gate_connector,
        rejection_notifier=gate_connector,
    )
    logger.info("[Feishu ingress] {} {}: {}", account.id, event.platform_event_id, result.outcome)
    return Response(status_code=status.HTTP_200_OK)


def _build_gate_connector(
    account: IMConnectorAccount,
    secrets: dict[str, Any],
    bot_open_id: str,
) -> FeishuConnector | None:
    """Construct a connector bound to a live lark Client for identity gating.

    Returns None and logs if ``lark_oapi`` isn't installed (defensive — the
    dep is required at runtime). The caller treats None as "skip the gate"
    so ingest falls back to ``acting_user_id`` instead of breaking.
    """
    try:
        import lark_oapi as _lark
        from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN
    except ImportError:
        logger.warning("[Feishu ingress] lark_oapi missing; identity gate disabled")
        return None
    domain = LARK_DOMAIN if str(secrets.get("domain", "feishu")) == "lark" else FEISHU_DOMAIN
    client = (
        _lark.Client.builder()
        .app_id(str(secrets["app_id"]))
        .app_secret(str(secrets["app_secret"]))
        .domain(domain)
        .log_level(_lark.LogLevel.WARNING)
        .build()
    )
    return FeishuConnector(bot_open_id=bot_open_id, client=client)


async def _redis_get(key: str) -> str | None:
    """Read a string from Redis, or None.

    Uses the application's already-configured async Redis client.
    """
    from cubebox.cache import get_redis

    client = get_redis()
    value = await client.get(key)
    if value is None:
        return None
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


async def _redis_setnx(key: str, value: str, ex: int) -> bool:
    """SETNX with TTL. Returns True iff the key was set (i.e., didn't pre-exist)."""
    from cubebox.cache import get_redis

    client = get_redis()
    return bool(await client.set(key, value, ex=ex, nx=True))


async def _handle_card_action(event: dict[str, Any]) -> tuple[bool, str | None]:
    """Process a ``card.action.trigger`` event.

    Returns ``(handled, toast)``. ``handled=True`` means we processed the
    event and the route should reply 200; ``toast`` is an optional
    user-visible message (Feishu shows it briefly above the card).
    """
    header = event.get("header") or {}
    token = str(header.get("token") or "")
    if not token:
        return True, "缺少 token"
    # Token replay guard — Feishu's interaction token is one-time / 30 minutes.
    fresh = await _redis_setnx(f"cardkit:token:{token}", "1", 1800)
    if not fresh:
        return True, None  # idempotent no-op

    try:
        payload = parse_action_payload(event.get("event") or {})
    except InvalidAction as exc:
        logger.warning("[Feishu ingress] invalid card.action payload: {}", exc)
        return True, "未知操作"

    expected = await _redis_get(f"run:{payload.run_id}:awaiting_responder")
    action = dispatch_card_action(payload, expected_responder_open_id=expected)
    if action is None:
        return True, "这不是发给你的"

    try:
        ok = await resume_paused_run(
            run_id=action.run_id,
            input_kind=action.input_kind,
            choice=action.choice,
            operator_open_id=action.operator_open_id,
            question_id=action.question_id,
        )
    except NotImplementedError:
        logger.warning("[Feishu ingress] resume_paused_run not implemented yet (Task 17)")
        return True, "暂时无法响应"
    except Exception:
        logger.warning("[Feishu ingress] resume_paused_run raised", exc_info=True)
        return True, "暂时无法响应"

    if not ok:
        return True, "会话已结束"
    return True, None
