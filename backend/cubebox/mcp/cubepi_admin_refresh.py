"""Persist MCP discovery result back to the DB row (admin/OAuth path)."""

from __future__ import annotations

from datetime import UTC, datetime

from cubebox.mcp.cubepi_admin_discovery import discover_tools_metadata
from cubebox.models import MCPServer
from cubebox.repositories.mcp import MCPServerRepository


async def refresh_tools_for_server_with_token(
    server: MCPServer,
    *,
    server_repo: MCPServerRepository,
    credential_or_token: str | None,
) -> None:
    """Run tool discovery against ``server`` and persist the result.

    Updates ``authed`` / ``tools_cache`` / ``last_error`` / ``last_discovered_at``
    and commits via the repository. Same contract as the deprecated
    ``cubebox.mcp.runtime.refresh_tools_for_server_with_token``.
    """
    success, tools, error = await discover_tools_metadata(
        server, credential_or_token=credential_or_token
    )
    server.authed = success
    server.tools_cache = tools or []

    # Strip orphan citation mapping keys whose tool no longer exists in
    # tools_cache. Only on success do we treat tools_cache as authoritative.
    # On failure, leave tool_citations alone so transient discovery errors
    # don't wipe user-edited mappings.
    notice: str | None = None
    if success:
        current_tool_names = {t["name"] for t in (server.tools_cache or [])}
        existing_citations = dict(server.tool_citations or {})
        orphans = sorted(k for k in existing_citations if k not in current_tool_names)
        if orphans:
            for k in orphans:
                existing_citations.pop(k, None)
            server.tool_citations = existing_citations
            notice = f"Removed citation mapping for vanished tools: {orphans!r}"

    server.last_error = notice if success else error
    server.last_discovered_at = datetime.now(UTC)
    await server_repo.update(server)
