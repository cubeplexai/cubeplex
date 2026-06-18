"""Pydantic schemas for IM channel binding CRUD."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChannelBindingCreateIn(BaseModel):
    channel_id: str = Field(min_length=1, max_length=128)
    channel_name: str = Field(default="", max_length=255)
    mode: str = Field(default="isolated", pattern="^(isolated|shared)$")
    sandbox_mode: str | None = Field(default=None, pattern="^(dedicated|creator)$")


class ChannelBindingUpdateIn(BaseModel):
    mode: str | None = Field(default=None, pattern="^(isolated|shared)$")
    sandbox_mode: str | None = Field(default=None, pattern="^(dedicated|creator)$")
    channel_name: str | None = Field(default=None, max_length=255)


class ChannelBindingOut(BaseModel):
    id: str
    account_id: str
    channel_id: str
    channel_name: str
    mode: str
    sandbox_mode: str | None
    topic_id: str | None
    created_at: str
    updated_at: str


class ChannelBindingListOut(BaseModel):
    bindings: list[ChannelBindingOut]
