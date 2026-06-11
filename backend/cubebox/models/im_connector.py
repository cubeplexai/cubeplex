"""IM connector models. Platform-neutral schema (Feishu first; Slack later).

Session boundary semantics live in `scope_key` — a connector-owned opaque
non-null string. See docs/dev/plans/2026-06-11-im-connectors-feishu.md
("Connector-neutral session boundary") for the contract.
"""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, DateTime, Index, text
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class IMConnectorAccount(CubeboxBase, OrgScopedMixin, table=True):
    """A bound IM bot account. One external IM account → one cubebox row."""

    _PREFIX: ClassVar[str] = "imac"
    __tablename__ = "im_connector_accounts"
    __table_args__ = (
        Index(
            "uq_im_account_platform_external",
            "platform",
            "external_account_id",
            unique=True,
        ),
        Index("ix_im_accounts_org_ws", "org_id", "workspace_id"),
    )

    platform: str = Field(max_length=16)  # 'feishu' | 'slack' | ...
    external_account_id: str = Field(max_length=128)  # Feishu app_id, Slack team_id, ...
    acting_user_id: str = Field(foreign_key="users.id", max_length=20)
    credential_id: str = Field(foreign_key="credentials.id", max_length=20)
    delivery_mode: str = Field(default="long_connection", max_length=24)
    enabled: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class IMThreadLink(CubeboxBase, OrgScopedMixin, table=True):
    """Durable map: (account, channel, connector-owned scope_key) → one cubebox Conversation.

    The table name keeps the historical 'thread_links' label, but scope_key is
    the actual session-boundary contract — see the plan's design intro.
    """

    _PREFIX: ClassVar[str] = "imtl"
    __tablename__ = "im_thread_links"
    __table_args__ = (
        Index(
            "uq_im_scope_link",
            "account_id",
            "channel_id",
            "scope_key",
            unique=True,
        ),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    channel_id: str = Field(max_length=128)
    scope_key: str = Field(max_length=255)
    scope_kind: str = Field(max_length=32)
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20, index=True)


class IMIdentityLink(CubeboxBase, OrgScopedMixin, table=True):
    """Map an IM sender (preferred: union_id) to a cubebox user.

    v1 falls back to account.acting_user_id when no link exists.
    """

    _PREFIX: ClassVar[str] = "imil"
    __tablename__ = "im_identity_links"
    __table_args__ = (Index("uq_im_identity_link", "account_id", "im_user_id", unique=True),)

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    im_user_id: str = Field(max_length=128)
    user_id: str = Field(foreign_key="users.id", max_length=20)


class IMWebhookReceipt(CubeboxBase, OrgScopedMixin, table=True):
    """Idempotency receipt keyed by platform event id (transactional outbox)."""

    _PREFIX: ClassVar[str] = "imwr"
    __tablename__ = "im_webhook_receipts"
    __table_args__ = (
        Index(
            "uq_im_receipt_account_event",
            "account_id",
            "platform_event_id",
            unique=True,
        ),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    platform_event_id: str = Field(max_length=255)
    status: str = Field(default="pending", max_length=16)
    lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class IMRunQueueItem(CubeboxBase, OrgScopedMixin, table=True):
    """Durable outbox row drained by IMRunQueueWorker via FOR UPDATE SKIP LOCKED."""

    _PREFIX: ClassVar[str] = "imrq"
    __tablename__ = "im_run_queue"
    __table_args__ = (
        Index(
            "ix_im_run_queue_pending",
            "status",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_im_run_queue_started_lease",
            "status",
            "claim_lease_expires_at",
            postgresql_where=text("status = 'started'"),
        ),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    receipt_id: str = Field(foreign_key="im_webhook_receipts.id", max_length=20, index=True)
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    content: str
    channel_id: str = Field(max_length=128)
    scope_key: str = Field(max_length=255)
    scope_kind: str = Field(max_length=32)
    reply_to_id: str | None = Field(default=None, max_length=128, nullable=True)
    inbound_message_id: str | None = Field(default=None, max_length=128, nullable=True)
    sender_im_user_id: str | None = Field(default=None, max_length=128, nullable=True)
    status: str = Field(default="pending", max_length=16)
    claimed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    claim_lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    attempts: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
