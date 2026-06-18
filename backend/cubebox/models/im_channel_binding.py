"""IM channel binding model.

Maps an IM channel (group chat) to its cubebox routing mode: 'isolated'
(one conversation per sender/thread) or 'shared' (one topic for the whole
channel).
"""

from typing import ClassVar

from sqlalchemy import Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin
from cubebox.models.public_id import PREFIX_IM_CHANNEL_BINDING


class IMChannelBinding(CubeboxBase, OrgScopedMixin, table=True):
    """Per-channel routing configuration for an IM connector account."""

    _PREFIX: ClassVar[str] = PREFIX_IM_CHANNEL_BINDING
    __tablename__ = "im_channel_bindings"
    __table_args__ = (
        Index(
            "uq_im_channel_binding",
            "account_id",
            "channel_id",
            unique=True,
        ),
        Index("ix_im_channel_binding_account", "account_id"),
    )

    account_id: str = Field(
        foreign_key="im_connector_accounts.id",
        max_length=20,
        ondelete="CASCADE",
    )
    channel_id: str = Field(max_length=128)
    channel_name: str = Field(default="", max_length=255)
    mode: str = Field(default="isolated", max_length=16)
    sandbox_mode: str | None = Field(default=None, max_length=16, nullable=True)
    topic_id: str | None = Field(
        default=None,
        foreign_key="topics.id",
        max_length=20,
        nullable=True,
    )
