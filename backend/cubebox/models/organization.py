"""Organization model — top-level tenant container."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class Organization(SQLModel, table=True):
    __tablename__ = "organizations"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    name: str = Field(max_length=255)
    slug: str = Field(max_length=32, unique=True, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
