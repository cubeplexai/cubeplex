"""IM runtime wiring: queue worker + per-account long-connection clients.

Moved out of ``cubebox/api/app.py`` so that the FastAPI factory only knows
how to start/stop the IM subsystem; everything about how IM is wired —
credential cache, lark client cache, CardKit token plumbing,
``OutboundRunTailer`` construction, ``FeishuLongConnection`` bootstrap —
lives here.

The two entry points are ``start(app, run_manager)`` and ``stop(app)``;
``app.state`` carries the dependencies (encryption backend, Redis, prefix).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI
from loguru import logger
from sqlalchemy import select

from cubebox.config import config as _config
from cubebox.credentials.dependencies import build_credential_service
from cubebox.db.engine import async_session_maker
from cubebox.im.artifacts import IMArtifactDispatcher
from cubebox.im.feishu.cardkit_client import CardKitClient
from cubebox.im.feishu.connector import FeishuConnector
from cubebox.im.feishu.long_connection import FeishuLongConnection
from cubebox.im.inbound import ingest_inbound_event
from cubebox.im.outbound import OutboundRunTailer
from cubebox.im.types import RenderState
from cubebox.im.worker import IMRunQueueWorker
from cubebox.models.im_connector import IMConnectorAccount


async def start(app: FastAPI, run_manager: Any) -> None:
    """Start the IM queue worker + per-account long-connection clients.

    Connect-each calls run concurrently via ``asyncio.gather`` so one slow
    or broken account does not stall startup. Failures are logged with full
    tracebacks; affected accounts simply won't receive long-connection
    traffic (webhook-mode accounts continue to work either way).
    """
    # Per-account decrypted-secret cache so we don't pay KDF + Feishu client
    # construction per turn. Keyed by (account_id, credential_id) — today
    # the only supported rotation path is delete-and-re-create the account
    # (CredentialService.create raises on the (org_id, kind, name) unique
    # index), which produces a new account_id; the credential_id leg of
    # the cache key is forward-looking for when ``upsert_by_kind_name`` is
    # wired through to ``connect_feishu``. Until then, restart the API to
    # flush a rotated credential.
    secret_cache: dict[tuple[str, str], dict[str, Any]] = {}
    client_cache: dict[tuple[str, str], Any] = {}

    async def _load_secrets(account: IMConnectorAccount) -> dict[str, Any]:
        key = (account.id, account.credential_id)
        if key in secret_cache:
            return secret_cache[key]
        async with async_session_maker() as s:
            svc = build_credential_service(
                s, app.state.encryption_backend, org_id=account.org_id, actor_user_id=None
            )
            plaintext = await svc.get_decrypted(
                credential_id=account.credential_id, requesting_kind="im_bot"
            )
        secrets: dict[str, Any] = json.loads(plaintext)
        secret_cache[key] = secrets
        return secrets

    def _client_for(account_key: tuple[str, str], secrets: dict[str, Any]) -> Any:
        if account_key in client_cache:
            return client_cache[account_key]
        import lark_oapi as _lark
        from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN

        domain = LARK_DOMAIN if str(secrets.get("domain", "feishu")) == "lark" else FEISHU_DOMAIN
        client = (
            _lark.Client.builder()
            .app_id(str(secrets["app_id"]))
            .app_secret(str(secrets["app_secret"]))
            .domain(domain)
            .log_level(_lark.LogLevel.WARNING)
            .build()
        )
        client_cache[account_key] = client
        return client

    async def _on_run_started(run_id: str, item: Any) -> None:
        async with async_session_maker() as s:
            account = (
                await s.execute(
                    select(IMConnectorAccount).where(IMConnectorAccount.id == item.account_id)
                )
            ).scalar_one()
        secrets = await _load_secrets(account)
        client = _client_for((account.id, account.credential_id), secrets)
        connector = FeishuConnector(
            bot_open_id=str(secrets.get("bot_open_id") or "") or None,
            client=client,
            channel_id=item.channel_id,
            reply_to_id=item.reply_to_id,
        )
        state = RenderState(
            bot_name="cubebox",
            run_id=run_id,
            reply_to_id=item.reply_to_id,
            inbound_message_id=item.inbound_message_id,
        )
        public_base = str(_config.get("api.public_url", "") or "")
        dispatcher = IMArtifactDispatcher(
            connector=connector,
            redis=app.state.redis,
            redis_key_prefix=app.state.redis_key_prefix,
            public_base_url=public_base,
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            conversation_id=item.conversation_id,
            card_state=state.card_state,
        )
        cardkit = _build_cardkit_client(client, secrets)
        from cubebox.im.feishu.op_dispatcher import FeishuOpDispatcher

        op_dispatcher = FeishuOpDispatcher(connector=connector, state=state, cardkit=cardkit)
        tailer = OutboundRunTailer(
            redis=app.state.redis,
            key_prefix=app.state.redis_key_prefix,
            run_id=run_id,
            connector=connector,
            state=state,
            dispatcher=op_dispatcher,
            artifact_dispatcher=dispatcher,
            responder_open_id=item.sender_open_id,
        )
        asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    worker = IMRunQueueWorker(
        session_maker=async_session_maker,
        run_manager=run_manager,
        on_run_started=_on_run_started,
        poll_interval=1.0,
        lease_seconds=300,
    )
    worker.start()
    app.state.im_run_queue_worker = worker
    app.state.im_long_connections = {}

    async def _connect_one(account: IMConnectorAccount) -> None:
        try:
            secrets = await _load_secrets(account)
            bot_open_id = str(secrets.get("bot_open_id") or "")
            if not bot_open_id:
                # Hydration failed at connect_feishu time. Without it the
                # parser's defense-in-depth mention gate falls through and
                # every group message becomes a run; AND the bot-echo guard
                # cannot recognize the bot's own outbound replies, looping
                # the agent on itself. Refuse to open the WebSocket.
                logger.warning(
                    "[IM] skipping long-connection for {} — bot_open_id not hydrated;"
                    " re-run connect_feishu to fix",
                    account.id,
                )
                return
            lc = FeishuLongConnection(
                account=account,
                app_id=str(secrets["app_id"]),
                app_secret=str(secrets["app_secret"]),
                bot_open_id=bot_open_id,
                ingest=ingest_inbound_event,
                session_maker=async_session_maker,
                run_manager=run_manager,
                redis_key_prefix=app.state.redis_key_prefix,
                domain=str(secrets.get("domain", "feishu")),
            )
            await lc.connect()
            app.state.im_long_connections[account.id] = lc
        except Exception:
            logger.exception("[IM] long-connection startup failed for account {}", account.id)

    async with async_session_maker() as s:
        accounts = (
            (
                await s.execute(
                    select(IMConnectorAccount).where(
                        IMConnectorAccount.platform == "feishu",  # type: ignore[arg-type]
                        IMConnectorAccount.delivery_mode == "long_connection",  # type: ignore[arg-type]
                        IMConnectorAccount.enabled == True,  # type: ignore[arg-type]  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
    if accounts:
        await asyncio.gather(*(_connect_one(a) for a in accounts), return_exceptions=True)

    # Expose the connector so the workspace POST /im/accounts route can
    # spin up the WebSocket inline instead of waiting for the next API
    # restart. Without this, creating an account via the API leaves it
    # dormant until a restart re-runs the bulk loop above.
    app.state.im_connect_account = _connect_one


def _build_cardkit_client(client: Any, secrets: dict[str, Any]) -> CardKitClient:
    """Construct a CardKitClient bound to the same Feishu/Lark domain + token
    cache as the lark_oapi client.

    ``TokenManager`` caches tenant_access_token in process memory (LocalCache),
    so the provider closure is effectively free after the first call.
    """
    from lark_oapi.core.const import LARK_DOMAIN as _LARK_DOMAIN
    from lark_oapi.core.token.manager import TokenManager as _TokenManager

    client_config = client.config
    client_domain = str(secrets.get("domain", "feishu"))
    base_url = _LARK_DOMAIN if client_domain == "lark" else "https://open.feishu.cn"

    def _token_provider() -> str:
        return str(_TokenManager.get_self_tenant_token(client_config))

    return CardKitClient(token_provider=_token_provider, base_url=base_url)


async def stop(app: FastAPI) -> None:
    """Stop IM long-connection clients then the queue worker."""
    long_conns = getattr(app.state, "im_long_connections", None) or {}
    for lc in long_conns.values():
        try:
            await lc.disconnect()
        except Exception:
            logger.warning("[IM] long-connection disconnect failed", exc_info=True)
    worker = getattr(app.state, "im_run_queue_worker", None)
    if worker is not None:
        try:
            await worker.stop()
        except Exception:
            logger.warning("[IM] queue worker stop failed", exc_info=True)


__all__ = ["start", "stop"]
