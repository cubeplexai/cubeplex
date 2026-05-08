"""V1 API Routes

Exports all v1 API routers.
"""

from cubebox.api.routes.v1 import (
    admin_mcp,
    admin_skills,
    mcp_catalog,
    mcp_oauth,
    ws_mcp,
    ws_settings,
    ws_skills,
)
from cubebox.api.routes.v1.admin import router as admin_router
from cubebox.api.routes.v1.artifacts import router as artifacts_router
from cubebox.api.routes.v1.attachments import router as attachments_router
from cubebox.api.routes.v1.auth import router as auth_router
from cubebox.api.routes.v1.conversations import router as conversations_router
from cubebox.api.routes.v1.memory import router as memory_router
from cubebox.api.routes.v1.workspaces import router as workspaces_router

__all__ = [
    "admin_router",
    "admin_mcp",
    "admin_skills",
    "mcp_catalog",
    "mcp_oauth",
    "artifacts_router",
    "attachments_router",
    "auth_router",
    "conversations_router",
    "memory_router",
    "workspaces_router",
    "ws_mcp",
    "ws_settings",
    "ws_skills",
]
