"""Organization model — top-level tenant container."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel

from cubebox.models.public_id import PREFIX_ORGANIZATION, generate_public_id


class Organization(SQLModel, table=True):
    __tablename__ = "organizations"

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_ORGANIZATION),
        primary_key=True,
        max_length=20,
    )
    name: str = Field(max_length=255)
    slug: str = Field(max_length=32, unique=True, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
