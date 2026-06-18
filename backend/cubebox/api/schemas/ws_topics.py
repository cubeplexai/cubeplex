"""Topic API schemas."""

from pydantic import BaseModel, Field


class TopicCreateRequest(BaseModel):
    title: str = Field(max_length=255)
    sandbox_mode: str | None = Field(default=None, pattern=r"^(dedicated|creator)$")
    member_user_ids: list[str] = Field(default_factory=list)


class TopicPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class TopicParticipantAddRequest(BaseModel):
    user_ids: list[str] = Field(min_length=1)


class TopicParticipantPatchRequest(BaseModel):
    role: str = Field(pattern=r"^(owner|member)$")


class UpgradeToTopicRequest(BaseModel):
    title: str = Field(max_length=255)
    sandbox_mode: str | None = Field(default=None, pattern=r"^(dedicated|creator)$")
    member_user_ids: list[str] = Field(default_factory=list)


class TopicConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    member_user_ids: list[str] = Field(default_factory=list)
