"""V1 API Routes

Exports all v1 API routers.
"""

from cubebox.api.routes.v1 import (
    admin_mcp,
    admin_members,
    admin_sandbox_env,
    admin_sandbox_policy,
    admin_skill_registries,
    admin_skills,
    admin_traces,
    mcp_oauth,
    public_artifacts,
    sandbox_share,
    shares,
    trigger_ingest,
    ws_browser,
    ws_mcp,
    ws_members,
    ws_sandbox,
    ws_sandbox_env,
    ws_scheduled_tasks,
    ws_settings,
    ws_skills,
    ws_topics,
    ws_triggers,
)
from cubebox.api.routes.v1.admin import router as admin_router
from cubebox.api.routes.v1.artifacts import router as artifacts_router
from cubebox.api.routes.v1.attachments import router as attachments_router
from cubebox.api.routes.v1.auth import router as auth_router
from cubebox.api.routes.v1.conversation_search import router as conversation_search_router
from cubebox.api.routes.v1.conversations import router as conversations_router
from cubebox.api.routes.v1.memory import router as memory_router
from cubebox.api.routes.v1.user_events import router as user_events_router
from cubebox.api.routes.v1.workspaces import router as workspaces_router

__all__ = [
    "admin_router",
    "admin_mcp",
    "admin_members",
    "admin_sandbox_env",
    "admin_sandbox_policy",
    "admin_skill_registries",
    "admin_skills",
    "admin_traces",
    "mcp_oauth",
    "artifacts_router",
    "attachments_router",
    "auth_router",
    "conversation_search_router",
    "conversations_router",
    "memory_router",
    "user_events_router",
    "public_artifacts",
    "sandbox_share",
    "shares",
    "trigger_ingest",
    "workspaces_router",
    "ws_browser",
    "ws_mcp",
    "ws_members",
    "ws_sandbox",
    "ws_sandbox_env",
    "ws_scheduled_tasks",
    "ws_settings",
    "ws_skills",
    "ws_topics",
    "ws_triggers",
]
