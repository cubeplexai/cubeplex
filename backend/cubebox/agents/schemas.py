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
    """Base model for agent events"""

    type: str = Field(description="Event type")
    timestamp: str = Field(description="ISO 8601 timestamp")
    data: dict[str, Any] = Field(description="Event data")


class ChainStartEvent(AgentEvent):
    """Event emitted when chain execution starts"""

    type: Literal["chain_start"] = "chain_start"
    data: dict[str, Any] = Field(description="Event data with input")


class LLMStartEvent(AgentEvent):
    """Event emitted when LLM call starts"""

    type: Literal["llm_start"] = "llm_start"
    data: dict[str, Any] = Field(description="Event data with model and messages")


class LLMEndEvent(AgentEvent):
    """Event emitted when LLM call ends"""

    type: Literal["llm_end"] = "llm_end"
    data: dict[str, Any] = Field(description="Event data with output and usage")


class ToolStartEvent(AgentEvent):
    """Event emitted when tool execution starts"""

    type: Literal["tool_start"] = "tool_start"
    data: dict[str, Any] = Field(description="Event data with tool name and input")


class ToolEndEvent(AgentEvent):
    """Event emitted when tool execution ends"""

    type: Literal["tool_end"] = "tool_end"
    data: dict[str, Any] = Field(description="Event data with tool name and output")


class ChainEndEvent(AgentEvent):
    """Event emitted when chain execution ends"""

    type: Literal["chain_end"] = "chain_end"
    data: dict[str, Any] = Field(description="Event data with output")


class ErrorEvent(AgentEvent):
    """Event emitted when an error occurs"""

    type: Literal["error"] = "error"
    data: dict[str, Any] = Field(description="Event data with error_code, message, and details")


class DoneEvent(AgentEvent):
    """Event emitted when execution is complete"""

    type: Literal["done"] = "done"
    data: dict[str, Any] = Field(default_factory=dict, description="Event data")
