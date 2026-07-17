"""SQLModel mixins and base class for cubeplex business tables."""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, Index
from sqlmodel import Field, SQLModel

from cubeplex.models.public_id import generate_public_id

_UTC_DT = DateTime(timezone=True)


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns.

    Use directly on tables with composite/non-prefixed PKs (e.g. association
    tables). Tables with a synthetic public-id PK get these for free via
    :class:`CubeplexBase`.
    """

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=_UTC_DT,
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=_UTC_DT,
    )


class CubeplexBase(SQLModel, TimestampMixin):
    """Base for business tables with a short prefixed public ID PK.

    Subclasses set ``_PREFIX`` (a 2–4 char lowercase string) and the ``id``
    column auto-fills with ``generate_public_id(_PREFIX)`` on construction.
    Inheritance order matters: ``class Foo(CubeplexBase, OrgScopedMixin, table=True)``.
    """

    __allow_unmapped__ = True

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
