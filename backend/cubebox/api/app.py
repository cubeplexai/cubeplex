"""FastAPI Application Factory

Creates and configures the FastAPI application with:
- Lifespan management (startup/shutdown)
- Middleware configuration
- Router registration
- Error handling
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from loguru import logger
from redis.asyncio import Redis

from cubebox.credentials.encryption import FernetBackend
from cubebox.utils import log


def _build_encryption_backend() -> FernetBackend:
    """Build the process-wide credential vault encryption backend."""
    from cubebox.config import config
    from cubebox.credentials.encryption import FernetBackend
    from cubebox.credentials.keys import parse_vault_keys

    raw_key = os.getenv("CUBEBOX_AUTH__VAULT_KEY") or config.get("auth.vault_key")
    if not raw_key or not str(raw_key).strip():
        raise RuntimeError(
            "CUBEBOX_AUTH__VAULT_KEY is required. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )

    return FernetBackend(parse_vault_keys(str(raw_key)))


def _build_mcp_user_token_signer() -> Any:
    """Build the process-wide MCP passthrough token signer."""
    from cubebox.mcp.dependencies import build_user_token_signer

    return build_user_token_signer()


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore
    """
    Application lifespan manager.
    Handles startup and shutdown events.
    """
    # ==================== Startup ====================
    log.init()
    logger.info("Application starting up")
    _app.state.encryption_backend = _build_encryption_backend()
    _app.state.mcp_user_token_signer = _build_mcp_user_token_signer()
    from cubebox.audit.sink import NoOpAuditSink

    _app.state.audit_sink = NoOpAuditSink()

    # NOTE: we deliberately do NOT install signal handlers here. Uvicorn's
    # own SIGTERM / SIGINT handlers trigger graceful shutdown, which awaits
    # this lifespan's shutdown phase below. Drain happens there. Installing
    # our own handlers via loop.add_signal_handler shadowed uvicorn's
    # handlers and prevented the shutdown sequence from ever firing — the
    # process would log "entering drain mode" and then sit forever because
    # uvicorn never learned the signal arrived.
    #
    # Double Ctrl-C in dev: uvicorn already handles this natively
    # (the second SIGINT flips force_exit and tears the server down).

    # Discover + bind plugin registry; mount AuthProvider routers.
    from typing import cast

    from cubebox.config import config as _cubebox_config
    from cubebox.plugins import get_registry
    from cubebox.plugins.protocols import AdminPanelExtension
    from cubebox.plugins.protocols import AuthProvider as _AuthProvider

    _reg = get_registry()
    await _reg.discover()
    _reg.bind_defaults(config=_cubebox_config)
    _auth_provider = _reg.get_auth_provider()
    assert isinstance(_auth_provider, _AuthProvider)
    _auth_routers = _auth_provider.get_auth_routers()
    for _auth_router in _auth_routers:
        _app.include_router(_auth_router, prefix="/api/v1")
    logger.info("Mounted {} AuthProvider router(s)", len(_auth_routers))

    # Mount the admin-extensions manifest endpoint + each extension's router/static.
    from fastapi.staticfiles import StaticFiles

    from cubebox.api.routes.v1 import admin_extensions

    _app.include_router(admin_extensions.router, prefix="/api/v1")

    for _ext_obj in _reg.get_admin_panel_extensions():
        _ext = cast(AdminPanelExtension, _ext_obj)
        _plugin_name = type(_ext).__module__.split(".")[0]
        _ext_router = _ext.get_router()
        if _ext_router is not None:
            _app.include_router(
                _ext_router,
                prefix=f"/api/v1/admin/_extensions/{_plugin_name}",
            )
        _ext_static = _ext.get_static_path()
        if _ext_static is not None:
            _app.mount(
                f"/api/v1/admin/_extensions/{_plugin_name}/static",
                StaticFiles(directory=str(_ext_static)),
            )
    logger.info(
        "Mounted {} AdminPanelExtension(s)",
        len(_reg.get_admin_panel_extensions()),
    )

    # Load MCP tools into the global registry
    from cubebox.tools import init_mcp_tools

    await init_mcp_tools()

    redis_client: Redis | None = None
    run_manager = None
    try:
        redis_factory = getattr(_app.state, "redis_factory", None)
        if redis_factory is not None:
            redis_client = redis_factory()
        else:
            from cubebox.config import config

            redis_client = Redis.from_url(
                config.get("redis.url", "redis://localhost:6379/0"),
                decode_responses=True,
                max_connections=config.get("redis.max_connections", 64),
                socket_timeout=config.get("redis.socket_timeout_seconds", 10),
                socket_connect_timeout=config.get("redis.socket_connect_timeout_seconds", 5),
                socket_keepalive=config.get("redis.socket_keepalive", True),
                health_check_interval=config.get("redis.health_check_interval_seconds", 30),
                retry_on_timeout=config.get("redis.retry_on_timeout", True),
            )
        ping_result = redis_client.ping()
        if isinstance(ping_result, Awaitable):
            await ping_result
        _app.state.redis = redis_client

        # Share the client with non-route code (parsers/dedup, filebox) via
        # the module-level accessor. Same client object, no second connection.
        from cubebox.cache import set_redis as _set_shared_redis

        _set_shared_redis(redis_client)

        import os

        from cubebox.config import config
        from cubebox.streams.run_manager import RunManager

        base_prefix = config.get("redis.key_prefix", "cubebox")
        env_name = os.getenv("ENV_FOR_DYNACONF", "development")
        _app.state.redis_key_prefix = f"{base_prefix}:{env_name}"
        run_manager = RunManager(
            app=_app,
            redis=redis_client,
            key_prefix=_app.state.redis_key_prefix,
            run_event_ttl_seconds=config.get("streaming.run_event_ttl_seconds", 43200),
            run_stream_max_events=config.get("streaming.run_stream_max_events", 1000000),
        )
        _app.state.run_manager = run_manager
        logger.info(
            "Redis streaming runtime initialized (prefix={})",
            _app.state.redis_key_prefix,
        )
    except Exception as e:
        logger.error("Failed to initialize Redis streaming runtime: {}", str(e))
        raise

    # Discover file parser plugins (text / notebook / docling)
    try:
        from cubebox.parsers import get_parser_registry

        await get_parser_registry().discover()
        logger.info("Parser registry initialized")
    except Exception as e:
        logger.error("Failed to initialize parser registry: {}", str(e))
        raise

    # Initialize LangGraph checkpointer (creates pool + setup tables)
    try:
        from cubebox.agents.checkpointer import init_checkpointer

        await init_checkpointer()
        logger.info("LangGraph checkpointer initialized")
    except Exception as e:
        logger.error("Failed to initialize LangGraph checkpointer: {}", str(e))
        raise

    # Initialize SandboxManager and start cleanup loop
    cleanup_task = None
    try:
        from cubebox.config import config
        from cubebox.db.engine import async_session_maker
        from cubebox.sandbox.manager import init_sandbox_manager

        sandbox_enabled = config.get("sandbox.enabled", False)
        if sandbox_enabled:
            manager = init_sandbox_manager(async_session_maker)
            logger.info("SandboxManager initialized")

            # Start background cleanup task
            from cubebox.sandbox.cleanup import sandbox_cleanup_loop

            cleanup_interval = config.get("sandbox.cleanup_interval", 60)
            cleanup_task = asyncio.create_task(
                sandbox_cleanup_loop(manager, interval=cleanup_interval)
            )
            logger.info("Sandbox cleanup loop started")
    except Exception as e:
        logger.warning("Failed to initialize SandboxManager: {}", str(e))

    # Seed preinstalled skills into the global catalog (idempotent, lock-guarded).
    try:
        from pathlib import Path

        from cubebox.config import backend_dir, config
        from cubebox.db.engine import async_session_maker
        from cubebox.seeders import seed_preinstalled_skills

        preinstalled_rel = config.get("skills.preinstalled_dir", "skills/preinstalled")
        preinstalled_dir = Path(backend_dir) / preinstalled_rel
        async with async_session_maker() as seed_session:
            await seed_preinstalled_skills(
                preinstalled_dir=preinstalled_dir,
                db_session=seed_session,
                redis=redis_client,
            )
        logger.info("Preinstalled skill seed step completed")
    except Exception as e:
        logger.warning("Failed to seed preinstalled skills: {}", str(e))

    # Seed system providers from config.yaml (idempotent).
    try:
        from cubebox.db import async_session_maker
        from cubebox.seeders import seed_system_providers_from_config

        async with async_session_maker() as seed_session:
            await seed_system_providers_from_config(seed_session, _app.state.encryption_backend)
        logger.info("System provider seed step completed")
    except Exception as e:
        logger.warning("Failed to seed system providers: {}", str(e))

    # M7: orphan attachment reaper
    from cubebox.config import config
    from cubebox.services.attachments import cleanup_orphan_attachments

    _attachment_cleanup_task: asyncio.Task[None] | None = None

    async def _attachment_cleanup_loop() -> None:
        interval = int(config.get("attachments.cleanup_interval_seconds", 300))
        while True:
            try:
                await cleanup_orphan_attachments()
            except Exception as exc:  # noqa: BLE001
                logger.warning("attachment cleanup failed: {}", exc)
            await asyncio.sleep(interval)

    _attachment_cleanup_task = asyncio.create_task(
        _attachment_cleanup_loop(), name="attachment-cleanup"
    )

    yield

    # ==================== Shutdown ====================
    logger.info("Application shutting down")
    from cubebox.agents.checkpointer import shutdown_checkpointer

    await shutdown_checkpointer()
    if _attachment_cleanup_task is not None:
        _attachment_cleanup_task.cancel()
        try:
            await _attachment_cleanup_task
        except asyncio.CancelledError:
            pass
    if run_manager is not None:
        from cubebox.config import config as _lifecycle_config

        _app.state.drain_state.enter_draining()
        drain_timeout = _lifecycle_config.get("lifecycle.graceful_drain_timeout_seconds", 3600)
        await run_manager.drain(timeout_seconds=float(drain_timeout))
    if redis_client is not None:
        await redis_client.aclose()
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        logger.info("Sandbox cleanup loop stopped")


def create_app(
    checkpointer_factory: Callable[[], Any] | None = None,
    sandbox_factory: Callable[[], Any] | None = None,
    redis_factory: Callable[[], Redis] | None = None,
) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        checkpointer_factory: Optional factory for dependency injection (testing).
        sandbox_factory: Optional factory for dependency injection (testing).

    Returns:
        Configured FastAPI application instance
    """
    app = FastAPI(
        title="cubebox API",
        description="AI Agent System Backend",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Store DI factories for route handlers
    app.state.checkpointer_factory = checkpointer_factory
    app.state.sandbox_factory = sandbox_factory
    app.state.redis_factory = redis_factory

    # Drain state must be created before middleware registration so the
    # DrainMiddleware can capture the same instance the lifespan + signal
    # handlers will write to (add_middleware runs at construction time,
    # before the lifespan starts).
    from cubebox.lifecycle.drain import DrainState

    app.state.drain_state = DrainState()

    # Register middleware
    from cubebox.api.middleware.cancellation import CancellationMiddleware
    from cubebox.api.middleware.csrf import CSRFMiddleware
    from cubebox.api.middleware.drain import DrainMiddleware
    from cubebox.api.middleware.rate_limit import limiter
    from cubebox.api.middleware.user_identity import UserIdentityMiddleware

    app.add_middleware(CancellationMiddleware)
    app.add_middleware(UserIdentityMiddleware)
    app.add_middleware(CSRFMiddleware)
    # Registered last → outermost on the request path. A draining server
    # refuses new runs before any other middleware does work.
    app.add_middleware(DrainMiddleware, drain_state=app.state.drain_state)

    # Wire slowapi limiter into app state + exception handler
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # Register exception handlers
    from cubebox.api.exceptions import register_exception_handlers

    register_exception_handlers(app)

    # Register routers
    from cubebox.api.routes.v1 import (
        admin_mcp,
        admin_providers,
        admin_router,
        admin_skills,
        artifacts_router,
        attachments_router,
        conversations_router,
        workspaces_router,
        ws_mcp,
        ws_settings,
        ws_skills,
    )

    app.include_router(workspaces_router, prefix="/api/v1")
    app.include_router(conversations_router, prefix="/api/v1")
    app.include_router(artifacts_router, prefix="/api/v1")
    app.include_router(attachments_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(admin_mcp.router, prefix="/api/v1")
    app.include_router(admin_skills.router, prefix="/api/v1")
    app.include_router(admin_skills.bindings_router, prefix="/api/v1")
    app.include_router(ws_mcp.router, prefix="/api/v1")
    app.include_router(ws_settings.router, prefix="/api/v1")
    app.include_router(admin_providers.router, prefix="/api/v1")
    app.include_router(ws_skills.router, prefix="/api/v1")

    from cubebox.api.routes.health import router as health_router

    app.include_router(health_router)

    return app
