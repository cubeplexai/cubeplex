"""V1 API Routes

Exports all v1 API routers.
"""

from cubebox.api.routes.v1.conversations import router as conversations_router

__all__ = ["conversations_router"]
