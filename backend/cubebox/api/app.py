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

    # Initialize LangGraph checkpointer for conversation persistence
    checkpointer_cm = None
    try:
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver

        from cubebox.agents.checkpointer import set_checkpointer
        from cubebox.db.engine import _build_database_url

        # aiomysql connection string (strip the driver prefix)
        raw_url = _build_database_url()
        conn_string = raw_url.replace("mysql+aiomysql://", "mysql+aiomysql://")

        checkpointer_cm = AIOMySQLSaver.from_conn_string(conn_string)
        checkpointer = await checkpointer_cm.__aenter__()
        await checkpointer.setup()
        set_checkpointer(checkpointer)
        logger.info("LangGraph checkpointer initialized")
    except Exception as e:
        logger.warning("Failed to initialize LangGraph checkpointer: {}", str(e))

    yield

    # ==================== Shutdown ====================
    if checkpointer_cm is not None:
        try:
            await checkpointer_cm.__aexit__(None, None, None)
            logger.info("LangGraph checkpointer shut down")
        except Exception as e:
            logger.warning("Error shutting down checkpointer: {}", str(e))

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
