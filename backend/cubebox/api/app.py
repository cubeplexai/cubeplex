"""FastAPI Application Factory

Creates and configures the FastAPI application with:
- Lifespan management (startup/shutdown)
- Middleware configuration
- Router registration
- Error handling
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from loguru import logger
from redis.asyncio import Redis

from cubebox.utils import log


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore
    """
    Application lifespan manager.
    Handles startup and shutdown events.
    """
    # ==================== Startup ====================
    log.init()
    logger.info("Application starting up")

    # Load MCP tools into the global registry
    from cubebox.tools import init_mcp_tools

    await init_mcp_tools()

    # Load builtin skills and store on app state
    from cubebox.config import backend_dir, config
    from cubebox.middleware.skills import load_builtin_skills

    if config.get("sandbox.skills.enabled", True):
        skills_dir = backend_dir / config.get("sandbox.skills.builtin_dir", "skills/builtin")
        _app.state.skills = load_builtin_skills(skills_dir)
        logger.info("Loaded {} builtin skill(s)", len(_app.state.skills))
    else:
        _app.state.skills = []

    redis_client: Redis | None = None
    run_manager = None
    try:
        redis_factory = getattr(_app.state, "redis_factory", None)
        if redis_factory is not None:
            redis_client = redis_factory()
        else:
            from cubebox.config import config

            redis_client = Redis.from_url(
                config.get("streaming.redis_url", "redis://localhost:6379/0"),
                decode_responses=True,
            )
        ping_result = redis_client.ping()
        if isinstance(ping_result, Awaitable):
            await ping_result
        _app.state.redis = redis_client

        # Share the client with non-route code (parsers/dedup, filebox) via
        # the module-level accessor. Same client object, no second connection.
        from cubebox.cache import set_redis as _set_shared_redis

        _set_shared_redis(redis_client)

        from cubebox.config import config
        from cubebox.streams.run_manager import RunManager

        _app.state.redis_key_prefix = config.get("streaming.redis_key_prefix", "cubebox")
        run_manager = RunManager(
            app=_app,
            redis=redis_client,
            key_prefix=_app.state.redis_key_prefix,
            run_event_ttl_seconds=config.get("streaming.run_event_ttl_seconds", 900),
        )
        _app.state.run_manager = run_manager
        logger.info("Redis streaming runtime initialized")
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

    # Initialize LangGraph checkpointer tables (one-time setup)
    try:
        import aiomysql
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver

        from cubebox.config import config

        # Build connection parameters
        conn_params = {
            "host": config.get("database.host", "localhost"),
            "port": config.get("database.port", 3306),
            "user": config.get("database.user", "root"),
            "password": config.get("database.password", ""),
            "db": config.get("database.name", "cubebox"),
            "autocommit": True,
        }

        # Create temporary connection to setup tables
        setup_conn = await aiomysql.connect(**conn_params)
        try:
            checkpointer = AIOMySQLSaver(conn=setup_conn)
            await checkpointer.setup()
            logger.info("LangGraph checkpointer tables initialized")
        finally:
            setup_conn.close()
    except Exception as e:
        logger.warning("Failed to initialize LangGraph checkpointer tables: {}", str(e))

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

    yield

    # ==================== Shutdown ====================
    logger.info("Application shutting down")
    if run_manager is not None:
        await run_manager.shutdown()
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

    # Register middleware
    from cubebox.api.middleware.cancellation import CancellationMiddleware
    from cubebox.api.middleware.csrf import CSRFMiddleware
    from cubebox.api.middleware.rate_limit import limiter
    from cubebox.api.middleware.user_identity import UserIdentityMiddleware

    app.add_middleware(CancellationMiddleware)
    app.add_middleware(UserIdentityMiddleware)
    app.add_middleware(CSRFMiddleware)

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
        artifacts_router,
        auth_router,
        conversations_router,
        workspaces_router,
    )

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(workspaces_router, prefix="/api/v1")
    app.include_router(conversations_router, prefix="/api/v1")
    app.include_router(artifacts_router, prefix="/api/v1")

    return app
