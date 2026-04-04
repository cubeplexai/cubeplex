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
