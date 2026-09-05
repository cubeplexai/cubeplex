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

from cubeplex.config import config as _config
from cubeplex.credentials.dependencies import build_credential_service
from cubeplex.db.engine import async_session_maker
from cubeplex.im.feishu.cardkit_client import CardKitClient
from cubeplex.im.worker import IMRunQueueWorker
from cubeplex.models.im_connector import IMConnectorAccount

# ---------------------------------------------------------------------------
# Distributed lease constants
# ---------------------------------------------------------------------------
LEASE_TTL = 30
SWEEP_INTERVAL = 15
CONNECTION_HEARTBEAT_TTL = 45

_ACQUIRE_LEASE_LUA = """
-- cubeplex-im-acquire
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
local owner = redis.call('GET', KEYS[1])
if not owner then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
  return 1
end
if owner == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""

_COMPARE_EXPIRE_LUA = """
-- cubeplex-im-renew
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""

_COMPARE_DELETE_LUA = """
-- cubeplex-im-release
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

_SUSPEND_LUA = """
-- cubeplex-im-suspend
redis.call('SET', KEYS[2], '1')
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('DEL', KEYS[1])
end
if redis.call('GET', KEYS[3]) == ARGV[1] then
  redis.call('DEL', KEYS[3])
end
return 1
"""


def _lease_key(prefix: str, account_id: str) -> str:
    return f"{prefix}:im:gateway:{account_id}:owner"


def _suspension_key(prefix: str, account_id: str) -> str:
    return f"{prefix}:im:gateway:{account_id}:suspended"


def _heartbeat_key(prefix: str, account_id: str) -> str:
    return f"{prefix}:im:gateway:{account_id}:connected"


# ---------------------------------------------------------------------------
# Distributed lease helpers (module-level, tested independently)
# ---------------------------------------------------------------------------


async def try_acquire_lease(redis: Any, *, account_id: str, instance_id: str, prefix: str) -> bool:
    """Atomically claim/renew ownership unless terminal suspension is set."""
    result = await redis.eval(
        _ACQUIRE_LEASE_LUA,
        2,
        _lease_key(prefix, account_id),
        _suspension_key(prefix, account_id),
        instance_id,
        LEASE_TTL,
    )
    return bool(result)


async def release_lease(redis: Any, *, account_id: str, instance_id: str, prefix: str) -> None:
    """Release lease only if we still own it (compare-and-delete)."""
    await redis.eval(
        _COMPARE_DELETE_LUA,
        1,
        _lease_key(prefix, account_id),
        instance_id,
    )


async def renew_lease(redis: Any, *, account_id: str, instance_id: str, prefix: str) -> bool:
    """Extend the TTL on a lease we own. Returns False if we lost it."""
    result = await redis.eval(
        _COMPARE_EXPIRE_LUA,
        1,
        _lease_key(prefix, account_id),
        instance_id,
        LEASE_TTL,
    )
    return bool(result)


async def suspend_connection(
    redis: Any,
    *,
    account_id: str,
    instance_id: str,
    prefix: str,
) -> None:
    """Atomically suspend reconnect and release this instance's live state."""
    await redis.eval(
        _SUSPEND_LUA,
        3,
        _lease_key(prefix, account_id),
        _suspension_key(prefix, account_id),
        _heartbeat_key(prefix, account_id),
        instance_id,
    )


async def clear_connection_suspension(redis: Any, *, account_id: str, prefix: str) -> None:
    await redis.delete(_suspension_key(prefix, account_id))


async def publish_connection_heartbeat(
    redis: Any,
    *,
    account_id: str,
    instance_id: str,
    prefix: str,
) -> None:
    await redis.set(
        _heartbeat_key(prefix, account_id),
        instance_id,
        ex=CONNECTION_HEARTBEAT_TTL,
    )


async def remove_connection_heartbeat(
    redis: Any,
    *,
    account_id: str,
    instance_id: str,
    prefix: str,
) -> None:
    await redis.eval(
        _COMPARE_DELETE_LUA,
        1,
        _heartbeat_key(prefix, account_id),
        instance_id,
    )


async def read_connection_heartbeats(
    redis: Any,
    *,
    account_ids: list[str],
    prefix: str,
) -> set[str]:
    if not account_ids:
        return set()
    values = await redis.mget([_heartbeat_key(prefix, account_id) for account_id in account_ids])
    return {account_id for account_id, value in zip(account_ids, values, strict=True) if value}


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
    import cubeplex.im.dingtalk  # noqa: F401
    import cubeplex.im.discord  # noqa: F401
    import cubeplex.im.feishu  # noqa: F401
    import cubeplex.im.slack  # noqa: F401
    import cubeplex.im.teams  # noqa: F401
    import cubeplex.im.wecom  # noqa: F401

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
    owned_accounts: set[str] = set()
    deliverable_accounts: set[str] = set()
    connection_locks: dict[str, asyncio.Lock] = {}

    def _transport_for(account_id: str) -> Any:
        return gateways.get(account_id) or app.state.im_long_connections.get(account_id)

    def _transport_is_open(account_id: str) -> bool:
        transport = _transport_for(account_id)
        check = getattr(transport, "is_open", None)
        if transport is None or not callable(check):
            return False
        try:
            return bool(check())
        except Exception:
            logger.opt(exception=True).warning(
                "[IM] connection health check failed for {}",
                account_id,
            )
            return False

    async def _connection_opened(account_id: str) -> None:
        still_owned = await renew_lease(
            app.state.redis,
            account_id=account_id,
            instance_id=instance_id,
            prefix=app.state.redis_key_prefix,
        )
        if not still_owned:
            owned_accounts.discard(account_id)
            deliverable_accounts.discard(account_id)
            await remove_connection_heartbeat(
                app.state.redis,
                account_id=account_id,
                instance_id=instance_id,
                prefix=app.state.redis_key_prefix,
            )
            raise RuntimeError(f"connection lease ownership lost for {account_id}")
        owned_accounts.add(account_id)
        if not _transport_is_open(account_id):
            return
        deliverable_accounts.add(account_id)
        await publish_connection_heartbeat(
            app.state.redis,
            account_id=account_id,
            instance_id=instance_id,
            prefix=app.state.redis_key_prefix,
        )

    async def _connection_closed(account_id: str) -> None:
        deliverable_accounts.discard(account_id)
        await remove_connection_heartbeat(
            app.state.redis,
            account_id=account_id,
            instance_id=instance_id,
            prefix=app.state.redis_key_prefix,
        )

    async def _terminal_disconnect(account_id: str) -> None:
        deliverable_accounts.discard(account_id)
        owned_accounts.discard(account_id)
        gateways.pop(account_id, None)
        await suspend_connection(
            app.state.redis,
            account_id=account_id,
            instance_id=instance_id,
            prefix=app.state.redis_key_prefix,
        )

    async def _validate_connection_lease(account_id: str) -> bool:
        owned = await renew_lease(
            app.state.redis,
            account_id=account_id,
            instance_id=instance_id,
            prefix=app.state.redis_key_prefix,
        )
        if not owned:
            owned_accounts.discard(account_id)
            deliverable_accounts.discard(account_id)
            await remove_connection_heartbeat(
                app.state.redis,
                account_id=account_id,
                instance_id=instance_id,
                prefix=app.state.redis_key_prefix,
            )
            return False
        if not _transport_is_open(account_id):
            deliverable_accounts.discard(account_id)
            await remove_connection_heartbeat(
                app.state.redis,
                account_id=account_id,
                instance_id=instance_id,
                prefix=app.state.redis_key_prefix,
            )
            return False
        owned_accounts.add(account_id)
        deliverable_accounts.add(account_id)
        return True

    async def _on_run_started(run_id: str, item: Any) -> None:
        from cubeplex.im.registry import get_platform

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

    from cubeplex.im.inbound_attachments import make_resolver

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
        deliverable_connection_ids=lambda: set(deliverable_accounts),
        validate_connection_lease=_validate_connection_lease,
    )
    worker.start()
    app.state.im_run_queue_worker = worker
    app.state.im_long_connections = {}
    app.state.im_owned_connections = owned_accounts
    app.state.im_deliverable_connections = deliverable_accounts
    app.state.im_instance_id = instance_id

    async def _connect_one(account: IMConnectorAccount) -> None:
        from cubeplex.im.registry import get_platform

        lock = connection_locks.setdefault(account.id, asyncio.Lock())
        async with lock:
            try:
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
                if _transport_is_open(account.id):
                    await _connection_opened(account.id)
                    return
                if _transport_for(account.id) is not None:
                    await _stop_local_connection(account.id, release=False)

                owned_accounts.add(account.id)
                secrets = await _load_secrets(account)
                platform = get_platform(account.platform)

                async def connection_opened() -> None:
                    await _connection_opened(account.id)

                async def connection_closed() -> None:
                    await _connection_closed(account.id)

                async def terminal_disconnect() -> None:
                    await _terminal_disconnect(account.id)

                await platform.on_account_enabled(
                    account,
                    secrets=secrets,
                    gateways=gateways,
                    session_maker=async_session_maker,
                    run_manager=run_manager,
                    redis_key_prefix=app.state.redis_key_prefix,
                    long_connections=app.state.im_long_connections,
                    app=app,
                    connection_opened=connection_opened,
                    connection_closed=connection_closed,
                    terminal_disconnect=terminal_disconnect,
                )
                if _transport_is_open(account.id):
                    await _connection_opened(account.id)
                else:
                    raise RuntimeError("connection did not become ready")
            except Exception:
                logger.exception(
                    "[IM] connection startup failed for account {} ({})",
                    account.id,
                    account.platform,
                )
                await _stop_local_connection(account.id, release=False)
                await release_lease(
                    app.state.redis,
                    account_id=account.id,
                    instance_id=instance_id,
                    prefix=app.state.redis_key_prefix,
                )

    async def _stop_local_connection(account_id: str, *, release: bool) -> None:
        gateway = gateways.pop(account_id, None)
        if gateway is not None:
            try:
                await gateway.stop()
            except Exception:
                logger.opt(exception=True).warning(
                    "[IM] gateway shutdown failed for {}",
                    account_id,
                )
        connection = app.state.im_long_connections.pop(account_id, None)
        if connection is not None:
            try:
                await connection.disconnect()
            except Exception:
                logger.opt(exception=True).warning(
                    "[IM] long-connection shutdown failed for {}",
                    account_id,
                )
        deliverable_accounts.discard(account_id)
        owned_accounts.discard(account_id)
        await remove_connection_heartbeat(
            app.state.redis,
            account_id=account_id,
            instance_id=instance_id,
            prefix=app.state.redis_key_prefix,
        )
        if release:
            await release_lease(
                app.state.redis,
                account_id=account_id,
                instance_id=instance_id,
                prefix=app.state.redis_key_prefix,
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
            from cubeplex.im.registry import get_platform as _get_platform

            platform = _get_platform(wa.platform)
            await platform.on_account_enabled(wa, secrets=secrets, gateways=gateways)
        except Exception:
            logger.opt(exception=True).warning(
                "[IM] teams app init failed for account {} on startup",
                wa.id,
            )

    # Expose the connector so the workspace POST /im/accounts route can
    # spin up the connection inline instead of waiting for the next restart.
    async def _enable_account(account: IMConnectorAccount) -> None:
        await clear_connection_suspension(
            app.state.redis,
            account_id=account.id,
            prefix=app.state.redis_key_prefix,
        )
        await _connect_one(account)

    async def _disable_account(account_id: str) -> None:
        await _stop_local_connection(account_id, release=True)
        await clear_connection_suspension(
            app.state.redis,
            account_id=account_id,
            prefix=app.state.redis_key_prefix,
        )

    app.state.im_connect_account = _enable_account
    app.state.im_disconnect_account = _disable_account
    app.state.im_gateways = gateways

    # ----- Lease sweep task: renew owned leases, claim orphans -----
    async def _sweep_once() -> None:
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
        enabled_by_id = {account.id: account for account in all_accounts}
        for account_id in set(owned_accounts) | set(gateways) | set(app.state.im_long_connections):
            if account_id not in enabled_by_id:
                await _stop_local_connection(account_id, release=True)

        for acct in all_accounts:
            if acct.id in owned_accounts:
                owned = await renew_lease(
                    app.state.redis,
                    account_id=acct.id,
                    instance_id=instance_id,
                    prefix=app.state.redis_key_prefix,
                )
                if not owned:
                    await _stop_local_connection(acct.id, release=False)
                    continue
                if _transport_is_open(acct.id):
                    deliverable_accounts.add(acct.id)
                    await publish_connection_heartbeat(
                        app.state.redis,
                        account_id=acct.id,
                        instance_id=instance_id,
                        prefix=app.state.redis_key_prefix,
                    )
                else:
                    await _connection_closed(acct.id)
                continue

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

    app.state.im_reconcile_connections = _sweep_once

    async def _sweep() -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL)
            try:
                await _sweep_once()
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
    for lc in list(long_conns.values()):
        try:
            await lc.disconnect()
        except Exception:
            logger.opt(exception=True).warning("[IM] long-connection disconnect failed")

    # Stop gateways (Discord)
    gws = getattr(app.state, "im_gateways", None) or {}
    for gw in list(gws.values()):
        try:
            await gw.stop()
        except Exception:
            logger.opt(exception=True).warning("[IM] gateway stop failed")

    instance_id = getattr(app.state, "im_instance_id", None)
    prefix = getattr(app.state, "redis_key_prefix", "")
    owned = getattr(app.state, "im_owned_connections", None) or set()
    if instance_id:
        for account_id in list(owned):
            await remove_connection_heartbeat(
                app.state.redis,
                account_id=account_id,
                instance_id=instance_id,
                prefix=prefix,
            )
            await release_lease(
                app.state.redis,
                account_id=account_id,
                instance_id=instance_id,
                prefix=prefix,
            )
    owned.clear()
    deliverable = getattr(app.state, "im_deliverable_connections", None) or set()
    deliverable.clear()

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
    "suspend_connection",
    "clear_connection_suspension",
    "publish_connection_heartbeat",
    "remove_connection_heartbeat",
    "read_connection_heartbeats",
    "LEASE_TTL",
]
