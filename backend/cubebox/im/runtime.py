"""IM runtime wiring: queue worker + per-account connection clients.

Platform-agnostic entry point that dispatches to registered
``PlatformConnector`` implementations (Feishu, Discord, …) via the
platform registry. Each platform handles its own tailer construction,
connection lifecycle, and credential interpretation.

Distributed ownership is managed via a Redis lease: each API instance
generates a unique ``instance_id`` and uses ``try_acquire_lease`` /
``renew_lease`` to claim accounts. A periodic sweep re-acquires orphan
leases so that a crashed instance's accounts are picked up by a survivor.

The two entry points are ``start(app, run_manager)`` and ``stop(app)``;
``app.state`` carries the dependencies (encryption backend, Redis, prefix).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import FastAPI
from loguru import logger
from sqlalchemy import select

from cubebox.config import config as _config
from cubebox.credentials.dependencies import build_credential_service
from cubebox.db.engine import async_session_maker
from cubebox.im.feishu.cardkit_client import CardKitClient
from cubebox.im.worker import IMRunQueueWorker
from cubebox.models.im_connector import IMConnectorAccount

# ---------------------------------------------------------------------------
# Distributed lease constants
# ---------------------------------------------------------------------------
LEASE_TTL = 30
SWEEP_INTERVAL = 15


# ---------------------------------------------------------------------------
# Distributed lease helpers (module-level, tested independently)
# ---------------------------------------------------------------------------


async def try_acquire_lease(redis: Any, *, account_id: str, instance_id: str, prefix: str) -> bool:
    """Claim ownership of *account_id* via NX, or confirm we already own it."""
    key = f"{prefix}:im:gateway:{account_id}:owner"
    if await redis.set(key, instance_id, nx=True, ex=LEASE_TTL):
        return True
    current = await redis.get(key)
    if current is not None:
        decoded = current.decode() if isinstance(current, bytes) else current
        if decoded == instance_id:
            await redis.expire(key, LEASE_TTL)
            return True
    return False


async def release_lease(redis: Any, *, account_id: str, instance_id: str, prefix: str) -> None:
    """Release lease only if we still own it (compare-and-delete)."""
    key = f"{prefix}:im:gateway:{account_id}:owner"
    current = await redis.get(key)
    if current is not None:
        decoded = current.decode() if isinstance(current, bytes) else current
        if decoded == instance_id:
            await redis.delete(key)


async def renew_lease(redis: Any, *, account_id: str, instance_id: str, prefix: str) -> bool:
    """Extend the TTL on a lease we own. Returns False if we lost it."""
    key = f"{prefix}:im:gateway:{account_id}:owner"
    current = await redis.get(key)
    if current is not None:
        decoded = current.decode() if isinstance(current, bytes) else current
        if decoded == instance_id:
            await redis.expire(key, LEASE_TTL)
            return True
    return False


# ---------------------------------------------------------------------------
# Feishu-specific helpers (kept at module level so FeishuPlatform can import)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


async def start(app: FastAPI, run_manager: Any) -> None:
    """Start the IM queue worker + per-account connection clients.

    Connect-each calls run concurrently via ``asyncio.gather`` so one slow
    or broken account does not stall startup. Failures are logged with full
    tracebacks; affected accounts simply won't receive connection traffic.
    """
    # Trigger platform registrations
    import cubebox.im.dingtalk  # noqa: F401
    import cubebox.im.discord  # noqa: F401
    import cubebox.im.feishu  # noqa: F401
    import cubebox.im.slack  # noqa: F401
    import cubebox.im.teams  # noqa: F401

    instance_id = str(uuid.uuid4())

    # Per-account decrypted-secret cache so we don't pay KDF + client
    # construction per turn.
    secret_cache: dict[tuple[str, str], dict[str, Any]] = {}
    client_cache: dict[tuple[str, str], Any] = {}

    async def _load_secrets(account: IMConnectorAccount) -> dict[str, Any]:
        key = (account.id, account.credential_id)
        if key in secret_cache:
            return secret_cache[key]
        async with async_session_maker() as s:
            svc = build_credential_service(
                s,
                app.state.encryption_backend,
                org_id=account.org_id,
                actor_user_id=None,
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

    # Dict of account_id → gateway object (Discord) or long-connection (Feishu)
    gateways: dict[str, Any] = {}

    async def _on_run_started(run_id: str, item: Any) -> None:
        from cubebox.im.registry import get_platform

        async with async_session_maker() as s:
            account = (
                await s.execute(
                    select(IMConnectorAccount).where(IMConnectorAccount.id == item.account_id)
                )
            ).scalar_one()

        try:
            platform = get_platform(account.platform)
        except KeyError:
            logger.warning(
                "[IM] unsupported platform {} for run {}",
                account.platform,
                run_id,
            )
            return

        await platform.build_tailer(
            run_id=run_id,
            queue_item=item,
            account=account,
            redis=app.state.redis,
            key_prefix=app.state.redis_key_prefix,
            session_maker=async_session_maker,
            run_manager=run_manager,
            secret_cache=secret_cache,
            client_cache=client_cache,
            load_secrets=_load_secrets,
            config=_config,
            gateways=gateways,
            app=app,
        )

    from cubebox.im.inbound_attachments import make_resolver

    resolve_inbound_attachments = make_resolver(
        session_maker=async_session_maker,
        load_secrets=_load_secrets,
        client_for=_client_for,
    )

    worker = IMRunQueueWorker(
        session_maker=async_session_maker,
        run_manager=run_manager,
        on_run_started=_on_run_started,
        resolve_inbound_attachments=resolve_inbound_attachments,
        poll_interval=1.0,
        lease_seconds=300,
    )
    worker.start()
    app.state.im_run_queue_worker = worker
    app.state.im_long_connections = {}

    async def _connect_one(account: IMConnectorAccount) -> None:
        from cubebox.im.registry import get_platform

        try:
            secrets = await _load_secrets(account)
            platform = get_platform(account.platform)

            acquired = await try_acquire_lease(
                app.state.redis,
                account_id=account.id,
                instance_id=instance_id,
                prefix=app.state.redis_key_prefix,
            )
            if not acquired:
                logger.debug(
                    "[IM] lease for {} owned by another instance, skipping",
                    account.id,
                )
                return

            await platform.on_account_enabled(
                account,
                secrets=secrets,
                gateways=gateways,
                session_maker=async_session_maker,
                run_manager=run_manager,
                redis_key_prefix=app.state.redis_key_prefix,
                long_connections=app.state.im_long_connections,
                app=app,
            )
        except Exception:
            logger.exception(
                "[IM] connection startup failed for account {} ({})",
                account.id,
                account.platform,
            )

    # Query all enabled accounts with connection-based delivery
    async with async_session_maker() as s:
        accounts = (
            (
                await s.execute(
                    select(IMConnectorAccount).where(
                        IMConnectorAccount.enabled == True,  # type: ignore[arg-type]  # noqa: E712
                        IMConnectorAccount.delivery_mode.in_(  # type: ignore[attr-defined]
                            ["long_connection", "gateway", "stream"]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
    if accounts:
        await asyncio.gather(*(_connect_one(a) for a in accounts), return_exceptions=True)

    # Initialize Teams webhook App instances (no persistent connection,
    # but the App instance must exist for the ingress route to dispatch).
    async with async_session_maker() as s:
        webhook_accounts = (
            (
                await s.execute(
                    select(IMConnectorAccount).where(
                        IMConnectorAccount.enabled == True,  # type: ignore[arg-type]  # noqa: E712
                        IMConnectorAccount.platform == "teams",  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
    for wa in webhook_accounts:
        try:
            secrets = await _load_secrets(wa)
            from cubebox.im.registry import get_platform as _get_platform

            platform = _get_platform(wa.platform)
            await platform.on_account_enabled(wa, secrets=secrets, gateways=gateways)
        except Exception:
            logger.opt(exception=True).warning(
                "[IM] teams app init failed for account {} on startup",
                wa.id,
            )

    # Expose the connector so the workspace POST /im/accounts route can
    # spin up the connection inline instead of waiting for the next restart.
    app.state.im_connect_account = _connect_one
    app.state.im_gateways = gateways

    # ----- Lease sweep task: renew owned leases, claim orphans -----
    async def _sweep() -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL)
            try:
                async with async_session_maker() as s:
                    all_accounts = (
                        (
                            await s.execute(
                                select(IMConnectorAccount).where(
                                    IMConnectorAccount.enabled == True,  # type: ignore[arg-type]  # noqa: E712
                                    IMConnectorAccount.delivery_mode.in_(  # type: ignore[attr-defined]
                                        ["long_connection", "gateway", "stream"]
                                    ),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                for acct in all_accounts:
                    owned = await renew_lease(
                        app.state.redis,
                        account_id=acct.id,
                        instance_id=instance_id,
                        prefix=app.state.redis_key_prefix,
                    )
                    if not owned:
                        acquired = await try_acquire_lease(
                            app.state.redis,
                            account_id=acct.id,
                            instance_id=instance_id,
                            prefix=app.state.redis_key_prefix,
                        )
                        if acquired:
                            logger.info(
                                "[IM] claimed orphan lease for {} ({})",
                                acct.id,
                                acct.platform,
                            )
                            await _connect_one(acct)
            except Exception:
                logger.opt(exception=True).warning("[IM] lease sweep failed")

    sweep_task = asyncio.create_task(_sweep(), name="im-lease-sweep")
    app.state.im_lease_sweep = sweep_task


async def stop(app: FastAPI) -> None:
    """Stop IM gateway/long-connection clients, sweep task, then the queue worker."""
    # Stop sweep
    sweep = getattr(app.state, "im_lease_sweep", None)
    if sweep is not None:
        sweep.cancel()
        try:
            await sweep
        except (asyncio.CancelledError, Exception):
            pass

    # Stop long-connections (Feishu)
    long_conns = getattr(app.state, "im_long_connections", None) or {}
    for lc in long_conns.values():
        try:
            await lc.disconnect()
        except Exception:
            logger.opt(exception=True).warning("[IM] long-connection disconnect failed")

    # Stop gateways (Discord)
    gws = getattr(app.state, "im_gateways", None) or {}
    for gw in gws.values():
        try:
            await gw.stop()
        except Exception:
            logger.opt(exception=True).warning("[IM] gateway stop failed")

    # Stop worker
    worker = getattr(app.state, "im_run_queue_worker", None)
    if worker is not None:
        try:
            await worker.stop()
        except Exception:
            logger.opt(exception=True).warning("[IM] queue worker stop failed")


__all__ = [
    "start",
    "stop",
    "try_acquire_lease",
    "release_lease",
    "renew_lease",
    "LEASE_TTL",
]
