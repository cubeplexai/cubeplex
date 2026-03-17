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
    from cubebox.api.routes.v1 import agents_router, conversations_router

    app.include_router(agents_router, prefix="/api/v1")
    app.include_router(conversations_router, prefix="/api/v1")

    return app
