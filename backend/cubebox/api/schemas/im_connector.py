"""Pydantic schemas for the IM connector workspace + admin routes."""

from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator, Field, Tag


class ConnectFeishuAccountIn(BaseModel):
    """Payload for ``POST /ws/{ws}/im/accounts`` when ``platform == 'feishu'``.

    ``app_id`` is also the ``external_account_id`` Feishu uses. ``bot_open_id``
    is hydrated by the server at connect time (via ``/open-apis/bot/v3/info``)
    and stored on the credential — clients never supply it. ``acting_user_id``
    accepts the sentinel ``"self"`` which the route maps to ``ctx.user.id``.
    """

    platform: Literal["feishu"] = "feishu"
    app_id: str = Field(min_length=1, max_length=128)
    app_secret: str = Field(min_length=1)
    encrypt_key: str = ""
    verification_token: str = ""
    domain: str = Field(default="feishu", pattern="^(feishu|lark)$")
    delivery_mode: str = Field(default="long_connection", pattern="^(long_connection|webhook)$")
    acting_user_id: str = Field(default="self", min_length=1)


class ConnectDiscordAccountIn(BaseModel):
    """Payload for ``POST /ws/{ws}/im/accounts`` when ``platform == 'discord'``."""

    platform: Literal["discord"] = "discord"
    bot_token: str = Field(min_length=1)
    application_id: str = Field(min_length=1, max_length=128)
    acting_user_id: str = Field(default="self", min_length=1)


class ImRuntimeStatus(BaseModel):
    """Runtime status snapshot embedded on every ``IMAccountOut``.

    Computed per-request at list time — not persisted. ``connection_state``
    is calculated from ``app.state.im_long_connections`` (long-connection
    mode) or a recent-receipts window (webhook mode); the other fields are
    cheap aggregate queries against existing IM tables. See spec §5 + §8.
    """

    connection_state: Literal["connected", "disconnected", "never_connected"]
    last_inbound_at: str | None
    bot_open_id: str | None
    pending_queue: int
    matched_24h: int
    rejected_24h: int

    @classmethod
    def unknown(cls) -> "ImRuntimeStatus":
        """Default placeholder. Used by single-account routes that return
        an ``IMAccountOut`` without running the aggregate-query path
        (POST /accounts, /disable, /enable) — the frontend's next list
        poll repopulates the real values within seconds.
        """
        return cls(
            connection_state="never_connected",
            last_inbound_at=None,
            bot_open_id=None,
            pending_queue=0,
            matched_24h=0,
            rejected_24h=0,
        )


class IMAccountOut(BaseModel):
    """Public projection of an ``IMConnectorAccount`` row + runtime status."""

    id: str
    platform: str
    external_account_id: str
    workspace_id: str
    acting_user_id: str
    delivery_mode: str
    enabled: bool
    runtime: ImRuntimeStatus
    bot_app_name: str | None = None
    bot_avatar_url: str | None = None


class IMAccountListOut(BaseModel):
    accounts: list[IMAccountOut]


ConnectIMAccountIn = Annotated[
    Annotated[ConnectFeishuAccountIn, Tag("feishu")]
    | Annotated[ConnectDiscordAccountIn, Tag("discord")],
    Discriminator("platform"),
]
