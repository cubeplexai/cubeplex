"""Registered remote skill registries (org-scoped admin config)."""

from typing import ClassVar

from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase


class SkillRegistry(CubeboxBase, table=True):
    """A remote registry an org admin registered for skill discovery.

    The built-in local catalog adapter is implicit (always present) and has no
    row here — only admin-configured registries are persisted.
    """

    _PREFIX: ClassVar[str] = "sksrc"
    __tablename__ = "skill_registries"

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    name: str = Field(max_length=128)
    kind: str = Field(max_length=16, default="remote")
    base_url: str = Field(max_length=512)
    repo: str | None = Field(default=None, max_length=256)
    trust_tier: str = Field(max_length=16, default="untrusted")
    enabled: bool = Field(default=True)
    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )
