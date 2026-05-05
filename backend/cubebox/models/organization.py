"""Organization model — top-level tenant container."""

from typing import ClassVar

from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase


class Organization(CubeboxBase, table=True):
    """Organization is the top-level tenant. All other data hangs under it."""

    _PREFIX: ClassVar[str] = "org"
    __tablename__ = "organizations"

    name: str = Field(max_length=255)
    slug: str = Field(max_length=32, unique=True, index=True)
