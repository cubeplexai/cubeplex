"""Connector template service.

Thin domain wrapper around :class:`MCPConnectorTemplateRepository`. Used
by both admin and workspace routes when listing or resolving a template
before materializing an install.
"""

from cubeplex.models import MCPConnectorTemplate
from cubeplex.repositories.mcp import MCPConnectorTemplateRepository


class MCPConnectorTemplateService:
    """Read-only view over the global connector template catalog."""

    def __init__(self, repo: MCPConnectorTemplateRepository) -> None:
        self._repo = repo

    async def list_active(self) -> list[MCPConnectorTemplate]:
        """All templates currently usable for new installs."""
        return await self._repo.list_active()

    async def get_active(self, template_id: str) -> MCPConnectorTemplate:
        """Resolve a template by id, asserting it's still ``status='active'``.

        Raises ``ValueError("connector_template_not_found")`` if the row
        is missing or has been deprecated / disabled — the caller maps
        that to a 404 at the API boundary.
        """
        row = await self._repo.get(template_id)
        if row is None or row.status != "active":
            raise ValueError("connector_template_not_found")
        return row
