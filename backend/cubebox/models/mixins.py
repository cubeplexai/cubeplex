"""SQLModel mixins."""

from sqlalchemy import Index
from sqlmodel import Field


class OrgScopedMixin:
    """Mixin for tables that belong to an org + workspace.

    Adds org_id and workspace_id columns, both as real foreign keys to the
    parent tables. The shared composite index lives on each concrete table
    via ``__table_args__`` (see e.g. ``Conversation``).
    """

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", max_length=20, index=True)


def org_scope_index(table_name: str) -> Index:
    """Return a composite index on (org_id, workspace_id) for a table."""
    return Index(f"ix_{table_name}_org_ws", "org_id", "workspace_id")
