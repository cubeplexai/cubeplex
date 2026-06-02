"""Pydantic response schemas for conversation endpoints.

These are documentation of the frontend contract; the FastAPI route handlers
currently return ``dict[str, object]`` for back-compat with existing
fields. The serializer in :mod:`cubebox.streams.hitl_resume` produces a
dict that conforms to :data:`PendingHitl`.
"""

from typing import Literal

from pydantic import BaseModel


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
