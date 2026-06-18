"""User model — global identity (one row per email).

fastapi-users' ``SQLAlchemyBaseUserTable`` uses SQLAlchemy 2.0 ``Mapped[...]``
annotations which SQLModel/Pydantic cannot resolve, so we define the expected
columns directly on a SQLModel and let ``SQLAlchemyUserDatabase`` discover them
by name (``id``, ``email``, ``hashed_password``, ``is_active``, ``is_superuser``,
``is_verified``).
"""

from typing import ClassVar

from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase


class User(CubeboxBase, table=True):
    """User is a global identity; membership ties users to workspaces."""

    _PREFIX: ClassVar[str] = "usr"
    __tablename__ = "users"

    email: str = Field(max_length=320, unique=True, index=True)
    hashed_password: str = Field(max_length=1024)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    is_verified: bool = Field(default=False)
    language: str = Field(default="en", max_length=10)
    display_name: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=2048)
