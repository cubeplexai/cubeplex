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
    checkpointer_conn = None
    try:
        import aiomysql
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver

        from cubebox.agents.checkpointer import set_checkpointer
        from cubebox.config import config

        # Build connection parameters directly to avoid URL encoding issues
        # Note: AIOMySQLSaver.parse_conn_string has a bug - it doesn't unquote the password
        conn_params = {
            "host": config.get("database.host", "localhost"),
            "port": config.get("database.port", 3306),
            "user": config.get("database.user", "root"),
            "password": config.get("database.password", ""),
            "db": config.get("database.name", "cubebox"),
            "autocommit": True,
        }

        # Create connection and checkpointer manually
        checkpointer_conn = await aiomysql.connect(**conn_params)
        checkpointer = AIOMySQLSaver(conn=checkpointer_conn)
        await checkpointer.setup()
        set_checkpointer(checkpointer)
        logger.info("LangGraph checkpointer initialized")
    except Exception as e:
        logger.warning("Failed to initialize LangGraph checkpointer: {}", str(e))

    yield

    # ==================== Shutdown ====================
    if checkpointer_conn is not None:
        try:
            checkpointer_conn.close()
            logger.info("LangGraph checkpointer connection closed")
        except Exception as e:
            logger.warning("Error closing checkpointer connection: {}", str(e))

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
