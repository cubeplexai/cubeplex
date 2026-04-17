"""User model — global identity (one row per email)."""

from datetime import UTC, datetime

from fastapi_users.db import SQLAlchemyBaseUserTable
from sqlalchemy import Column, DateTime, String
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class User(SQLModel, SQLAlchemyBaseUserTable[str], table=True):
    """User identity. Inherits fastapi-users base columns:
    email, hashed_password, is_active, is_superuser, is_verified.
    We override id to use uuid7 string, and add created_at.
    """

    __tablename__ = "users"  # type: ignore[misc]  # fastapi-users declares `id: ID` under TYPE_CHECKING only; mypy sees it as an instance variable override

    id: str = Field(
        default_factory=lambda: str(uuid7()),
        sa_column=Column(String(32), primary_key=True),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime, nullable=False),
    )
