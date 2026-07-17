"""Organization model — top-level tenant container."""

from typing import ClassVar

from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase


class Organization(CubeplexBase, table=True):
    """Organization is the top-level tenant. All other data hangs under it."""

    _PREFIX: ClassVar[str] = "org"
    __tablename__ = "organizations"

    name: str = Field(max_length=255)
    slug: str = Field(max_length=32, unique=True, index=True)
