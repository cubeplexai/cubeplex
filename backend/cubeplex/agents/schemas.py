"""Request and Response Schemas for Agent Execution

Defines Pydantic models for API requests and streaming events.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExecuteRequest(BaseModel):
    """Request model for agent execution"""

    input: str = Field(description="User input question or task")
    sandbox_domain: str | None = Field(
        default=None, description="OpenSandbox server domain (e.g., 'localhost:8090')"
    )
    sandbox_image: str | None = Field(
        default=None,
        description="Docker image for sandbox (e.g., 'ubuntu:22.04')",
    )


class AgentEvent(BaseModel):
    """Base model for agent streaming events."""

    type: str = Field(description="Event type")
    timestamp: str = Field(description="ISO 8601 timestamp")
    data: dict[str, Any] = Field(description="Event data")
    agent_id: str | None = Field(
        default=None,
        description="None for main agent, 'subagent:<tool_call_id>' for subagents",
    )
    agent_name: str | None = Field(
        default=None,
        description="Human-readable subagent description",
    )


class TextDeltaEvent(AgentEvent):
    """Token-level text delta for streaming output.

    Emitted as LLM generates text tokens. Content is incremental.
    """

    type: Literal["text_delta"] = "text_delta"
    data: dict[str, Any] = Field(description="Event data with text delta and finish reason")


class ReasoningEvent(AgentEvent):
    """Model reasoning/thinking process output.

    Emitted when model generates reasoning content (if supported).
    """

    type: Literal["reasoning"] = "reasoning"
    data: dict[str, Any] = Field(description="Event data with reasoning content")


class ToolCallEvent(AgentEvent):
    """Tool invocation start event.

    Emitted when model decides to call a tool.
    """

    type: Literal["tool_call"] = "tool_call"
    data: dict[str, Any] = Field(
        description="Event data with tool name, arguments, and tool call id"
    )


class ToolCallDeltaEvent(AgentEvent):
    """Streaming tool call argument delta.

    Emitted as the LLM generates tool call arguments token-by-token.
    """

    type: Literal["tool_call_delta"] = "tool_call_delta"
    data: dict[str, Any] = Field(
        description="Event data with tool_call_id, name, args_delta, and index"
    )


class ToolResultEvent(AgentEvent):
    """Tool execution result event.

    Emitted after a tool finishes execution.
    """

    type: Literal["tool_result"] = "tool_result"
    data: dict[str, Any] = Field(description="Event data with tool name and result content")


class ArtifactEvent(AgentEvent):
    """Artifact lifecycle event.

    Emitted when the agent creates or updates an artifact via save_artifact tool.
    """

    type: Literal["artifact"] = "artifact"
    data: dict[str, Any] = Field(
        description="Artifact metadata: action (created|updated) and artifact object"
    )


class ErrorEvent(AgentEvent):
    """Event emitted when an error occurs"""

    type: Literal["error"] = "error"
    data: dict[str, Any] = Field(description="Event data with error_code, message, and details")


class DoneEvent(AgentEvent):
    """Event emitted when execution is complete"""

    type: Literal["done"] = "done"
    data: dict[str, Any] = Field(default_factory=dict, description="Event data")


class StatusEvent(AgentEvent):
    """Initialization phase status event.

    Emitted during setup (e.g., sandbox creation) before the LLM stream begins.
    """

    type: Literal["status"] = "status"
    data: dict[str, Any] = Field(description="Event data with phase identifier")


class CitationEvent(AgentEvent):
    """Citation reference event.

    Emitted when CitationMiddleware processes a tool result that has
    citation configuration. Contains source metadata and text chunks
    for frontend rendering of inline 【N-M】 references.
    """

    type: Literal["citation"] = "citation"
    data: dict[str, Any] = Field(
        description=(
            "Citation data: citation_id, chunks [{chunk_index, content}], "
            "metadata {source_type, url, title, ...}, tool_call_id"
        )
    )


class UsageEvent(AgentEvent):
    """Per-LLM-call token usage event.

    Emitted once per LLM call in a run, immediately after the final
    AIMessageChunk for that call. Carries the same dict shape that
    CostMiddleware computes via _extract_usage so consumers (cost UI,
    cache regression test) can read it without re-parsing the model
    response.
    """

    type: Literal["usage"] = "usage"
    data: dict[str, Any] = Field(
        description=(
            "Usage payload: input_tokens, output_tokens, cache_read_tokens, cache_write_tokens"
        )
    )


class InjectedMessageEvent(AgentEvent):
    """A user message injected mid-run (a steer) that cubepi has now drained
    into the thread. Carries the join key so the frontend can match it to a
    pending chip and commit it at the real transcript position.
    """

    type: Literal["injected_message"] = "injected_message"
    data: dict[str, Any] = Field(
        description=(
            "Event data with content and steer_id, plus sender_user_id and "
            "sender_display_name when the injected message came from a group chat"
        )
    )


class SandboxConfirmRequestEvent(AgentEvent):
    """A sandbox ``execute`` command matched a ``confirm`` rule and is paused
    awaiting human approval. The frontend renders an inline approve/deny card.
    """

    type: Literal["sandbox_confirm_request"] = "sandbox_confirm_request"
    data: dict[str, Any] = Field(
        description=(
            "Confirm request: question_id, tool_call_id, command, matched_pattern, timeout_seconds"
        )
    )


class SandboxConfirmResolvedEvent(AgentEvent):
    """A pending sandbox confirm was resolved (approved / denied / timed out /
    cancelled). The frontend flips the corresponding card to its final state.
    """

    type: Literal["sandbox_confirm_resolved"] = "sandbox_confirm_resolved"
    data: dict[str, Any] = Field(
        description=(
            "Resolution: question_id, decision, cancelled, timed_out, reason. "
            "decision is one of 'approve' | 'deny' | 'cancelled' | 'timed_out' | "
            "'policy_overridden' (last emitted by the respond-path dangling-pending "
            "cleanup when org sandbox policy changed mid-pause)."
        )
    )


class AskUserRequestEvent(AgentEvent):
    """The agent called the ask_user built-in tool and is paused waiting for
    the user to fill in the form. The frontend renders an AskUserCard.
    """

    type: Literal["ask_user_request"] = "ask_user_request"
    data: dict[str, Any] = Field(
        description=(
            "question_id, questions (list of {key,prompt,options,multi_select,required}), "
            "timeout_seconds"
        )
    )


class AskUserResolvedEvent(AgentEvent):
    """The pending ask_user was answered, cancelled, or timed out.
    The frontend removes the AskUserCard.
    """

    type: Literal["ask_user_resolved"] = "ask_user_resolved"
    data: dict[str, Any] = Field(
        description=(
            "question_id, answers ({key: value|[values]}|null), cancelled, timed_out. "
            "Optional reason: str carries non-answer outcomes such as 'policy_overridden' "
            "emitted by the respond-path dangling-pending cleanup."
        )
    )


class FailoverEvent(AgentEvent):
    """Model failover event — primary chain leg failed, switched to next.

    Emitted by the on_failover callback wired into FallbackBoundModel.
    data: {failed_ref, next_ref|None, reason}
    """

    type: Literal["model_failover"] = "model_failover"
    data: dict[str, Any] = Field(description="Event data with failed_ref, next_ref, reason")
