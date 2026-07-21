"""Pydantic models for /api/v1/admin/traces responses.

This module is the single source of truth for the cubepi-Tempo → frontend
view model mapping. The TempoClient parser writes into these types; the
frontend reads from them. Update both in lockstep when cubepi span
attributes change.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field


class SpanKind(StrEnum):
    AGENT = "agent"  # cubepi invoke_agent span
    TURN = "turn"  # cubepi.turn span
    CHAT = "chat"  # gen_ai chat span (LLM call)
    TOOL = "tool"  # execute_tool span
    OTHER = "other"  # anything else


class TokenUsage(BaseModel):
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0


class ChatMessage(BaseModel):
    role: str
    parts: list[dict[str, Any]] = Field(default_factory=list)


class ToolDefinition(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any] | None = None


class LlmCallPayload(BaseModel):
    """Detail carried by every `chat <model>` span."""

    model: str
    provider: str | None = None
    request_max_tokens: int | None = None
    request_temperature: float | None = None
    request_stream: bool | None = None
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    finish_reasons: list[str] = Field(default_factory=list)
    time_to_first_chunk_seconds: float | None = None
    response_id: str | None = None
    system_instructions: list[ChatMessage] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)
    output_messages: list[ChatMessage] = Field(default_factory=list)
    tools: list[ToolDefinition] = Field(default_factory=list)
    raw_request: str | None = None  # cubepi.llm.raw_request
    raw_response: str | None = None  # cubepi.llm.raw_response


class ToolCallPayload(BaseModel):
    """Detail carried by every `execute_tool` span."""

    name: str
    description: str | None = None
    arguments: str | None = None
    result: str | None = None
    is_error: bool = False
    execution_mode: str | None = None
    tool_call_id: str | None = None


class TurnPayload(BaseModel):
    index: int
    stop_reason: str | None = None
    tool_calls_count: int = 0
    messages: list[ChatMessage] = Field(default_factory=list)
    output_messages: list[ChatMessage] = Field(default_factory=list)


class AgentPayload(BaseModel):
    """Detail carried by the `invoke_agent` (run root) span."""

    provider: str | None = None
    tools: list[str] = Field(default_factory=list)
    system_instructions: list[ChatMessage] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)
    output_messages: list[ChatMessage] = Field(default_factory=list)


class SpanNode(BaseModel):
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: SpanKind
    start_time: AwareDatetime
    duration_ms: int
    status_code: str | None = None  # "OK" / "ERROR" / None
    status_message: str | None = None
    llm: LlmCallPayload | None = None
    tool: ToolCallPayload | None = None
    turn: TurnPayload | None = None
    agent: AgentPayload | None = None
    raw_attributes: dict[str, Any] = Field(default_factory=dict)
    children: list[SpanNode] = Field(default_factory=list)


class TraceSummary(BaseModel):
    trace_id: str
    root_name: str
    start_time: AwareDatetime
    duration_ms: int
    span_count: int
    org_id: str | None = None
    workspace_id: str | None = None
    user_id: str | None = None
    conversation_id: str | None = None
    run_id: str | None = None
    model: str | None = None
    has_error: bool = False


class TraceListResponse(BaseModel):
    # Tempo /api/search has no native cursor; we cap the page at `limit`.
    traces: list[TraceSummary]


class TraceDetail(BaseModel):
    summary: TraceSummary
    root: SpanNode


class TagValuesResponse(BaseModel):
    values: list[str]


class FilterOptionKind(StrEnum):
    """Entity kinds the traces filter dropdown can list from Postgres.

    `model` is intentionally absent - it is low-cardinality and sourced from
    Tempo via ``tag-values`` (the value is its own label). These three are
    Postgres-backed, org-scoped, and (for user/conversation) prefix-narrowed.
    """

    WORKSPACE = "workspace"
    USER = "user"
    CONVERSATION = "conversation"


class FilterOption(BaseModel):
    """One selectable entry: ``id`` is the filter value stored in the URL,
    ``name`` is the human-readable label shown in the dropdown."""

    id: str
    name: str


class FilterOptionsResponse(BaseModel):
    options: list[FilterOption]


SpanNode.model_rebuild()
