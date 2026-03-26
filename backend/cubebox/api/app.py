"""FastAPI Application Factory

Creates and configures the FastAPI application with:
- Lifespan management (startup/shutdown)
- Middleware configuration
- Router registration
- Error handling
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

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

    yield

    # ==================== Shutdown ====================
    logger.info("Application shutting down")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance
    """
    app = FastAPI(
        title="cubebox API",
        description="AI Agent System Backend with DeepAgents Framework",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Register exception handlers
    from cubebox.api.exceptions import register_exception_handlers

    register_exception_handlers(app)

    # Register routers
    from cubebox.api.routes.v1 import conversations_router

    app.include_router(conversations_router, prefix="/api/v1")

    return app
