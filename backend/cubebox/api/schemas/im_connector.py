"""Pydantic schemas for the IM connector workspace + admin routes (Task 15)."""

from pydantic import BaseModel, Field


class ConnectFeishuAccountIn(BaseModel):
    """Payload for ``POST /ws/{ws}/im/accounts`` when ``platform == 'feishu'``.

    ``app_id`` is also the ``external_account_id`` Feishu uses. ``bot_open_id``
    is hydrated by the server at connect time (via ``/open-apis/bot/v3/info``)
    and stored on the credential — clients never supply it. ``acting_user_id``
    accepts the sentinel ``"self"`` which the route maps to ``ctx.user.id``.
    """

    platform: str = Field(pattern="^feishu$")
    app_id: str = Field(min_length=1, max_length=128)
    app_secret: str = Field(min_length=1)
    encrypt_key: str = ""
    verification_token: str = ""
    domain: str = Field(default="feishu", pattern="^(feishu|lark)$")
    delivery_mode: str = Field(default="long_connection", pattern="^(long_connection|webhook)$")
    acting_user_id: str = Field(default="self", min_length=1)


class IMAccountOut(BaseModel):
    """Public projection of an ``IMConnectorAccount`` row."""

    id: str
    platform: str
    external_account_id: str
    workspace_id: str
    acting_user_id: str
    delivery_mode: str
    enabled: bool


class IMAccountListOut(BaseModel):
    accounts: list[IMAccountOut]
