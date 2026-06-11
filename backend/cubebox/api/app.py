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

    # Build the process-level cubepi Tracer once (None when tracing is disabled
    # or unavailable). Each run attaches/detaches it via cubepi.tracing.trace;
    # it is shut down in the shutdown phase below.
    from cubebox.agents.tracing import build_tracer

    _app.state.tracer = build_tracer()

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

    # MCP tools are assembled per agent run from DB-backed catalog/installs;
    # the legacy global registry loader was removed in M2.

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
        from cubebox.services.user_event_bus import UserEventBus

        _app.state.user_event_bus = UserEventBus()
        await run_manager.start_control_listeners()
        from cubebox.config import config as _sched_cfg
        from cubebox.schedules.poller import ScheduledTaskPoller

        poller = ScheduledTaskPoller(
            run_manager=run_manager,
            poll_interval_seconds=float(
                _sched_cfg.get("scheduled_tasks.poll_interval_seconds", 15.0)
            ),
            misfire_grace_seconds=int(_sched_cfg.get("scheduled_tasks.misfire_grace_seconds", 300)),
            claim_timeout_seconds=int(_sched_cfg.get("scheduled_tasks.claim_timeout_seconds", 120)),
            max_claims=int(_sched_cfg.get("scheduled_tasks.max_claims", 3)),
            busy_retry_delay_seconds=int(
                _sched_cfg.get("scheduled_tasks.busy_retry_delay_seconds", 300)
            ),
            max_busy_retries=int(_sched_cfg.get("scheduled_tasks.max_busy_retries", 3)),
        )
        poller.start()
        _app.state.scheduled_task_poller = poller
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

    # Initialize SandboxManager and start cleanup loop
    cleanup_task = None
    try:
        from cubebox.config import config
        from cubebox.db.engine import async_session_maker
        from cubebox.sandbox.manager import init_sandbox_manager

        sandbox_enabled = config.get("sandbox.enabled", False)
        if sandbox_enabled:
            manager = init_sandbox_manager(
                async_session_maker,
                _app.state.encryption_backend,
            )
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
            from cubebox.seeders.provider_seeder import seed_default_presets_from_config

            await seed_default_presets_from_config(seed_session)
        logger.info("System provider seed step completed")
    except Exception as e:
        logger.warning("Failed to seed system providers: {}", str(e))

    # Seed MCP connector templates (idempotent, lock-guarded).
    try:
        from cubebox.db.engine import async_session_maker
        from cubebox.seeders import seed_mcp_templates

        async with async_session_maker() as seed_session:
            await seed_mcp_templates(
                db_session=seed_session,
                backend=_app.state.encryption_backend,
                redis=redis_client,
            )
        logger.info("MCP template seed step completed")
    except Exception as e:
        logger.warning("Failed to seed MCP templates: {}", str(e))

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

    # Mode consistency check: refuse single_tenant if DB has >1 orgs
    mode = getattr(_app.state, "deployment_mode", "single_tenant")
    if mode == "single_tenant":
        from sqlalchemy import func, select

        from cubebox.db import async_session_maker
        from cubebox.models import Organization

        async with async_session_maker() as _session:
            _count = (
                await _session.execute(select(func.count()).select_from(Organization))
            ).scalar_one()
        if int(_count) > 1:
            raise RuntimeError(
                f"single_tenant requires exactly 0 or 1 orgs in DB; found "
                f"{int(_count)}. Switch to multi_tenant or clean up the DB "
                "before starting."
            )

    # Egress exchange mTLS listener (production only). Served on its own port so
    # the per-sandbox client-cert identity cannot be reached via the public API.
    _egress_listener = None
    from cubebox.config import config as _egress_cfg

    _egress_auth = dict(_egress_cfg.get("egress_exchange.auth", {}) or {})
    if _egress_auth.get("mode", "mtls") == "mtls":
        _lst = dict(_egress_cfg.get("egress_exchange.listener", {}) or {})
        if _lst.get("enabled", False):
            from cubebox.sandbox_env.exchange_listener import (
                ExchangeListener,
                build_exchange_app,
            )

            _exchange_app = build_exchange_app(
                encryption_backend=_app.state.encryption_backend,
                authenticator=_app.state.sidecar_authenticator,
            )
            _egress_listener = ExchangeListener(
                _exchange_app,
                host=_lst.get("host", "0.0.0.0"),
                port=int(_lst["port"]),
                certfile=_lst["certfile"],
                keyfile=_lst["keyfile"],
                ca_certs=_lst["ca_certs"],
            )
            await _egress_listener.start()
    _app.state._egress_listener = _egress_listener

    # Conversation-search embedding worker. Single instance on app.state so
    # route handlers (search endpoint) can reuse the provider's connection
    # pool, and the worker keeps draining embedding_jobs in the background.
    from cubebox.config import config as _search_cfg
    from cubebox.models.conversation_chunk import VECTOR_DIM
    from cubebox.search.embedding import EmbeddingProvider
    from cubebox.search.worker import EmbeddingWorker

    embedding_worker_task: asyncio.Task[None] | None = None
    _app.state.embedding_provider = None
    _app.state.embedding_worker = None
    _app.state.embedding_worker_task = None
    if _search_cfg.get("search.enabled", True):
        embedding_provider: EmbeddingProvider | None
        try:
            embedding_provider = EmbeddingProvider.from_config()
        except RuntimeError as exc:
            # Missing api key etc. — refuse to start the worker, route 503s.
            logger.critical("Embedding provider not started: {}", exc)
            embedding_provider = None
        if embedding_provider is not None and embedding_provider.dimensions != VECTOR_DIM:
            # Schema is frozen at 1024; config drift here would silently break
            # inserts. Refuse to start the worker and surface a critical log.
            logger.critical(
                "search.embedding.dimensions={} but schema VECTOR_DIM={}; refusing to start worker",
                embedding_provider.dimensions,
                VECTOR_DIM,
            )
            await embedding_provider.aclose()
            embedding_provider = None
        if embedding_provider is not None:
            _app.state.embedding_provider = embedding_provider
            embedding_worker = EmbeddingWorker(embedding_provider)
            embedding_worker_task = asyncio.create_task(
                embedding_worker.run(), name="embedding-worker"
            )
            _app.state.embedding_worker = embedding_worker
            # Expose the task so tests can cancel it cleanly before driving the
            # worker themselves with a deterministic provider.
            _app.state.embedding_worker_task = embedding_worker_task

    yield

    # ==================== Shutdown ====================
    logger.info("Application shutting down")
    # Tests may have cancelled the lifespan worker and cleared the state
    # references (see test_conversation_search_route.py). Re-read from
    # app.state so we don't blow up on the None.
    _live_worker = getattr(_app.state, "embedding_worker", None)
    _live_task: asyncio.Task[None] | None = getattr(_app.state, "embedding_worker_task", None)
    if _live_task is not None and _live_worker is not None:
        _live_worker.stop()
        try:
            await asyncio.wait_for(_live_task, timeout=5.0)
        except TimeoutError:
            _live_task.cancel()
            try:
                await _live_task
            except (asyncio.CancelledError, Exception):
                pass
    if _app.state.embedding_provider is not None:
        await _app.state.embedding_provider.aclose()
    _egress_listener = getattr(_app.state, "_egress_listener", None)
    if _egress_listener is not None:
        await _egress_listener.stop()
    if _attachment_cleanup_task is not None:
        _attachment_cleanup_task.cancel()
        try:
            await _attachment_cleanup_task
        except asyncio.CancelledError:
            pass
    if run_manager is not None:
        from cubebox.config import config as _lifecycle_config
        from cubebox.schedules.poller import ScheduledTaskPoller as _ScheduledTaskPoller

        _shutdown_poller: _ScheduledTaskPoller | None = getattr(
            _app.state, "scheduled_task_poller", None
        )
        if _shutdown_poller is not None:
            await _shutdown_poller.stop()
        _app.state.drain_state.enter_draining()
        drain_timeout = _lifecycle_config.get("lifecycle.graceful_drain_timeout_seconds", 3600)
        await run_manager.drain(timeout_seconds=float(drain_timeout))
        # Stop control listeners AFTER draining so in-flight runs can still be
        # cancelled/steered during graceful shutdown.
        await run_manager.stop_control_listeners()
    tracer = getattr(_app.state, "tracer", None)
    if tracer is not None:
        try:
            await tracer.shutdown()
        except Exception as exc:  # tracing teardown must never break shutdown
            logger.warning("Tracer shutdown failed: {}", exc)
    if redis_client is not None:
        await redis_client.aclose()
    mcp_oauth_http_client = getattr(_app.state, "_mcp_oauth_http_client", None)
    if mcp_oauth_http_client is not None:
        await mcp_oauth_http_client.aclose()
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        logger.info("Sandbox cleanup loop stopped")
    log.shutdown()


def create_app(
    sandbox_factory: Callable[[], Any] | None = None,
    redis_factory: Callable[[], Redis] | None = None,
) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        sandbox_factory: Optional factory for dependency injection (testing).
        redis_factory: Optional factory for dependency injection (testing).

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
    app.state.sandbox_factory = sandbox_factory
    app.state.redis_factory = redis_factory

    # Read deployment mode from config
    from cubebox.config import config as _cubebox_config

    _mode = str(_cubebox_config.get("deployment.mode", "single_tenant")).lower()
    if _mode not in ("single_tenant", "multi_tenant"):
        raise RuntimeError(
            f"Invalid deployment.mode={_mode!r}; must be 'single_tenant' or 'multi_tenant'"
        )
    app.state.deployment_mode = _mode

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
        admin_llm,
        admin_mcp,
        admin_members,
        admin_model_presets,
        admin_providers,
        admin_router,
        admin_sandbox_env,
        admin_sandbox_policy,
        admin_skill_registries,
        admin_skills,
        admin_traces,
        artifacts_router,
        attachments_router,
        conversation_search_router,
        conversations_router,
        mcp_oauth,
        memory_router,
        model_presets,
        public_artifacts,
        shares,
        system,
        trigger_ingest,
        user_events_router,
        workspaces_router,
        ws_browser,
        ws_mcp,
        ws_members,
        ws_sandbox,
        ws_sandbox_env,
        ws_scheduled_tasks,
        ws_settings,
        ws_skills,
        ws_triggers,
    )

    app.include_router(system.router, prefix="/api/v1")
    app.include_router(workspaces_router, prefix="/api/v1")
    # Search router goes first: it owns `/conversations/search`, while the
    # conversations router declares `/conversations/{conversation_id}` which
    # would otherwise swallow the literal `search` segment as an ID and 404.
    app.include_router(conversation_search_router, prefix="/api/v1")
    app.include_router(conversations_router, prefix="/api/v1")
    app.include_router(artifacts_router, prefix="/api/v1")
    app.include_router(public_artifacts.router, prefix="/api/v1")
    app.include_router(shares.router, prefix="/api/v1")
    app.include_router(attachments_router, prefix="/api/v1")
    app.include_router(memory_router, prefix="/api/v1")
    app.include_router(user_events_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(admin_members.router, prefix="/api/v1")
    app.include_router(admin_mcp.router, prefix="/api/v1")
    app.include_router(admin_sandbox_env.router, prefix="/api/v1")
    app.include_router(admin_sandbox_policy.router, prefix="/api/v1")
    # Public template list (authenticated, not org-admin gated).
    app.include_router(admin_mcp.public_templates_router, prefix="/api/v1")
    app.include_router(mcp_oauth.oauth_callback_router, prefix="/api/v1")
    app.include_router(admin_skill_registries.router, prefix="/api/v1")
    app.include_router(admin_skills.router, prefix="/api/v1")
    app.include_router(admin_skills.bindings_router, prefix="/api/v1")
    app.include_router(ws_mcp.router, prefix="/api/v1")
    app.include_router(ws_sandbox.router, prefix="/api/v1")
    app.include_router(ws_sandbox_env.router, prefix="/api/v1")
    app.include_router(ws_scheduled_tasks.router, prefix="/api/v1")
    app.include_router(ws_members.router, prefix="/api/v1")
    app.include_router(ws_settings.router, prefix="/api/v1")
    app.include_router(admin_providers.router, prefix="/api/v1")
    app.include_router(admin_llm.router, prefix="/api/v1")
    app.include_router(admin_model_presets.router, prefix="/api/v1")
    app.include_router(admin_traces.router, prefix="/api/v1")
    app.include_router(ws_skills.router, prefix="/api/v1")
    app.include_router(ws_triggers.router, prefix="/api/v1")
    app.include_router(model_presets.router, prefix="/api/v1")
    app.include_router(trigger_ingest.router, prefix="/api/v1")
    # Browser live-view/keepalive handlers require the SandboxManager, which is
    # only initialized when sandbox support is enabled (see the lifespan above).
    # Don't expose /browser/* otherwise — the handlers would 500 with
    # "SandboxManager not initialized". Gate the API surface to match capability.
    from cubebox.config import config as _sandbox_config

    if _sandbox_config.get("sandbox.enabled", False):
        app.include_router(ws_browser.router, prefix="/api/v1")

    from cubebox.api.routes.health import router as health_router

    app.include_router(health_router)

    # Internal sidecar-authenticated egress exchange endpoint.
    # The authenticator is built from config here (deployment_mode already set above)
    # so the prod guardrail fires at startup, not at request time.
    from cubebox.api.routes import internal_egress
    from cubebox.sandbox_env.exchange_auth import build_sidecar_authenticator

    _egress_auth_config = dict(_cubebox_config.get("egress_exchange.auth", {}) or {})
    app.state.sidecar_authenticator = build_sidecar_authenticator(
        _egress_auth_config,
        env=_cubebox_config.current_env,
    )
    # In dev (shared-secret) mode the exchange route is mounted on the public app
    # — there is no mTLS terminator locally. In mtls mode it is served ONLY by
    # the dedicated mTLS listener (started in the lifespan), never on the public
    # app, so the cert-bound sandbox identity cannot be bypassed via the public
    # port.
    if _egress_auth_config.get("mode", "mtls") == "dev":
        app.include_router(internal_egress.router, prefix="/api/v1")

    return app
