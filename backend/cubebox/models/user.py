"""User model — global identity (one row per email).

fastapi-users' ``SQLAlchemyBaseUserTable`` uses SQLAlchemy 2.0 ``Mapped[...]``
annotations which SQLModel/Pydantic cannot resolve, so we define the expected
columns directly on a SQLModel and let ``SQLAlchemyUserDatabase`` discover them
by name (``id``, ``email``, ``hashed_password``, ``is_active``, ``is_superuser``,
``is_verified``).
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=32)
    email: str = Field(max_length=320, unique=True, index=True)
    hashed_password: str = Field(max_length=1024)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)
    is_verified: bool = Field(default=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
