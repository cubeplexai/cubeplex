"""SQLModel mixins and base class for cubebox business tables."""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.orm import declared_attr
from sqlmodel import Field, SQLModel

from cubebox.models.public_id import generate_public_id


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns.

    Use directly on tables with composite/non-prefixed PKs (e.g. association
    tables). Tables with a synthetic public-id PK get these for free via
    :class:`CubeboxBase`.
    """

    __allow_unmapped__ = True

    @declared_attr
    def created_at(cls):  # type: ignore[no-untyped-def]
        return Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    @declared_attr
    def updated_at(cls):  # type: ignore[no-untyped-def]
        return Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class CubeboxBase(SQLModel, TimestampMixin):
    """Base for business tables with a short prefixed public ID PK.

    Subclasses set ``_PREFIX`` (a 2–4 char lowercase string) and the ``id``
    column auto-fills with ``generate_public_id(_PREFIX)`` on construction.
    Inheritance order matters: ``class Foo(CubeboxBase, OrgScopedMixin, table=True)``.
    """

    _PREFIX: ClassVar[str]

    id: str = Field(default="", primary_key=True, max_length=20)

    def model_post_init(self, __context: Any) -> None:
        """Auto-fill ``id`` with a prefixed public ID when not explicitly set."""
        if not self.id:
            # SQLModel table=True bypasses pydantic validators; use model_post_init
            # which *is* called by SQLModel's __init__.
            self.__dict__["id"] = generate_public_id(self.__class__._PREFIX)
        super().model_post_init(__context)


class OrgScopedMixin:
    """Mixin for tables that belong to an org + workspace.

    Adds ``org_id`` and ``workspace_id`` columns as real foreign keys to the
    parent tables. The shared composite index lives on each concrete table
    via ``__table_args__`` (see e.g. :class:`Conversation`).
    """

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", max_length=20, index=True)


def org_scope_index(table_name: str) -> Index:
    """Return a composite index on (org_id, workspace_id) for a table."""
    return Index(f"ix_{table_name}_org_ws", "org_id", "workspace_id")
