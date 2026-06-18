"""Pydantic response schemas for conversation endpoints.

These are documentation of the frontend contract; the FastAPI route handlers
currently return ``dict[str, object]`` for back-compat with existing
fields. The serializer in :mod:`cubebox.streams.hitl_resume` produces a
dict that conforms to :data:`PendingHitl`.
"""

from typing import Literal

from pydantic import BaseModel, Field


class InviteToGroupRequest(BaseModel):
    """Request body for inviting workspace members into a conversation."""

    user_ids: list[str] = Field(min_length=1, max_length=20)


class ConversationParticipantOut(BaseModel):
    """Hydrated conversation participant row."""

    id: str
    conversation_id: str
    user_id: str
    joined_at: str
    display_name: str | None = None
    email: str | None = None


class ListConversationParticipantsResponse(BaseModel):
    items: list[ConversationParticipantOut]


class AskUserOption(BaseModel):
    label: str
    value: str
    description: str | None = None
    allow_input: bool = False


class AskUserQuestion(BaseModel):
    key: str
    prompt: str
    options: list[AskUserOption] | None = None
    multi_select: bool
    required: bool


class PendingHitlAskUser(BaseModel):
    run_id: str
    question_id: str
    kind: Literal["ask_user"]
    requested_at: str  # ISO 8601 UTC, e.g. "2026-06-02T12:34:56+00:00"
    questions: list[AskUserQuestion]


class PendingHitlSandboxConfirm(BaseModel):
    run_id: str
    question_id: str
    kind: Literal["sandbox_confirm"]
    requested_at: str
    tool_call_id: str
    command: str
    matched_pattern: str


PendingHitl = PendingHitlAskUser | PendingHitlSandboxConfirm
