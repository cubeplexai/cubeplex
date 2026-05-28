"""Pydantic request/response schemas for workspace trigger routes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateTriggerIn(BaseModel):
    name: str = Field(max_length=128)
    webhook_secret: str  # plaintext — stored in credential vault; never echoed back
    prompt_template: str
    payload_fields: list[str] = []
    filter: dict[str, Any] | None = None
    run_as_user_id: str
    source_config: dict[str, Any] | None = None
    max_runs_per_minute: int = 10
    rate_limit_burst: int = 20
    rate_limit_response: Literal["429", "202_drop"] = "429"
    conversation_policy: Literal["new_each_time"] = "new_each_time"
    target_type: Literal["inline"] = "inline"
    source_type: Literal["webhook"] = "webhook"
    enabled: bool = True


class UpdateTriggerIn(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    prompt_template: str | None = None
    payload_fields: list[str] | None = None
    filter: dict[str, Any] | None = None  # None clears the filter
    run_as_user_id: str | None = None
    source_config: dict[str, Any] | None = None
    max_runs_per_minute: int | None = None
    rate_limit_burst: int | None = None
    rate_limit_response: Literal["429", "202_drop"] | None = None
    # conversation_policy, target_type, source_type intentionally omitted:
    # v1 only allows the single Literal value; callers cannot change them.


class RotateSecretIn(BaseModel):
    new_webhook_secret: str
    overlap_seconds: int = 86400  # 24h default


class TriggerOut(BaseModel):
    id: str
    name: str
    enabled: bool
    source_type: str
    source_config: dict[str, Any]
    target_type: str
    target_ref: dict[str, Any]
    payload_fields: list[str]
    filter: dict[str, Any] | None
    conversation_policy: str
    run_as_user_id: str
    max_runs_per_minute: int
    rate_limit_burst: int
    rate_limit_response: str
    current_secret_cred_id: str
    previous_secret_cred_id: str | None
    previous_secret_expires_at: str | None
    events_total: int
    events_success: int
    events_failed: int
    events_dedup_dropped: int
    created_at: str
    updated_at: str


class TriggerListOut(BaseModel):
    triggers: list[TriggerOut]


class TriggerEventOut(BaseModel):
    id: str
    trigger_id: str
    source_type: str
    event_type: str | None
    dedup_key: str
    occurred_at: str | None
    received_at: str
    status: str
    attempts: int
    last_error: str | None
    payload: dict[str, Any]
    resulting_run_id: str | None
    resulting_conversation_id: str | None


class TriggerEventListOut(BaseModel):
    events: list[TriggerEventOut]
    cursor: str | None = None


class RotateSecretOut(BaseModel):
    previous_secret_expires_at: str | None
    current_secret_cred_id: str
