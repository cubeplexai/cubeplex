"""DeepAgentExecutor

Core executor for running DeepAgent-based tasks with streaming support.
Handles agent creation, tool loading, and event streaming.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from langchain_core.tools import BaseTool
from loguru import logger

from cubebox.agents.schemas import (
    AgentEvent,
    ChainStartEvent,
    DoneEvent,
    ErrorEvent,
    ReasoningEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from cubebox.llm.factory import LLMFactory
from cubebox.tools import get_registry


class DeepAgentExecutor:
    """Executor for DeepAgent-based task execution with streaming support"""

    def __init__(
        self,
        *,
        sandbox: Any | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        """
        Initialize the DeepAgentExecutor.

        Creates LLM instance, loads tools from registry, and accepts an optional
        sandbox backend managed externally by SandboxManager.

        Args:
            sandbox: An OpenSandbox backend instance (managed by SandboxManager).
                    If None, the agent runs without sandbox.
            checkpointer: Optional LangGraph checkpoint saver for conversation persistence
        """
        logger.info("Initializing DeepAgentExecutor")
        self.llm = self._create_llm()
        self.tools = self._load_tools()
        self.checkpointer = checkpointer
        self._sandbox = sandbox
        logger.info("DeepAgentExecutor initialized with {} tools", len(self.tools))

    def _create_llm(self) -> Any:
        """
        Create LLM instance using LLMFactory.

        Uses the default model from configuration.

        Returns:
            LLM instance (ChatOpenAI or ChatOpenAICompatible)

        Raises:
            ValueError: If model creation fails
        """
        try:
            factory = LLMFactory()
            # Use the first available model from the first provider
            providers = factory.list_providers()
            if not providers:
                raise ValueError("No LLM providers configured")

            provider_name = providers[0]
            models = factory.list_models(provider_name)
            if not models:
                raise ValueError(f"No models available in provider '{provider_name}'")

            model_id = models[0]
            logger.info("Creating LLM with model: {} from provider: {}", model_id, provider_name)
            llm = factory.create(model_id=model_id, provider_name=provider_name)
            return llm
        except Exception as e:
            logger.error("Failed to create LLM: {}", str(e))
            raise

    def _load_tools(self) -> list[BaseTool]:
        """
        Load tools from the tool registry.

        Returns:
            List of BaseTool instances
        """
        try:
            registry = get_registry()
            tools = registry.list_tools()
            logger.info("Loaded {} tools from registry", len(tools))
            for tool in tools:
                logger.debug("Tool available: {}", tool.name)
            return tools
        except Exception as e:
            logger.error("Failed to load tools: {}", str(e))
            raise

    def _get_current_timestamp(self) -> str:
        """
        Get current timestamp in ISO 8601 format.

        Returns:
            ISO 8601 formatted timestamp string
        """
        return datetime.now(UTC).isoformat()

    def _handle_stream_chunk(self, chunk: Any) -> list[AgentEvent]:
        """
        Handle a single chunk from stream_mode="messages".

        Parses the chunk and yields appropriate events based on content type:
        - text_delta: Token-level text content
        - reasoning: Model reasoning/thinking
        - tool_call: Tool invocation
        - tool_result: Tool execution result

        Args:
            chunk: A tuple of (run_id, message_dict) from stream_mode="messages"

        Yields:
            AgentEvent instances
        """
        timestamp = self._get_current_timestamp()
        events: list[AgentEvent] = []

        # stream_mode="messages" returns (run_id, message_dict)
        if not isinstance(chunk, tuple) or len(chunk) < 2:
            return events

        _, msg = chunk
        if not isinstance(msg, dict):
            return events

        # Extract common fields
        content = msg.get("content", "")
        additional_kwargs = msg.get("additional_kwargs", {})
        response_metadata = msg.get("response_metadata", {})
        tool_calls = msg.get("tool_calls", [])
        finish_reason = response_metadata.get("finish_reason")
        chunk_position = msg.get("chunk_position")
        usage_metadata = msg.get("usage_metadata", {})

        # Check for reasoning content (thinking process)
        reasoning_content = additional_kwargs.get("reasoning_content", "")
        if reasoning_content:
            events.append(
                ReasoningEvent(
                    timestamp=timestamp,
                    data={"content": reasoning_content},
                )
            )
            logger.debug("[STREAM] Reasoning: {} chars", len(reasoning_content))

        # Check for tool call (model decided to call a tool)
        if tool_calls and finish_reason == "tool_calls":
            for tc in tool_calls:
                events.append(
                    ToolCallEvent(
                        timestamp=timestamp,
                        data={
                            "tool_call_id": tc.get("id", ""),
                            "name": tc.get("name", "unknown"),
                            "arguments": tc.get("args", {}),
                        },
                    )
                )
                logger.debug("[STREAM] Tool call: {}", tc.get("name", "unknown"))

        # Check for tool result (tool execution completed)
        # Tool messages have 'name' field set to tool name
        tool_name = msg.get("name")
        if tool_name and content:
            events.append(
                ToolResultEvent(
                    timestamp=timestamp,
                    data={
                        "tool_name": tool_name,
                        "content": content if isinstance(content, str) else str(content),
                    },
                )
            )
            logger.debug("[STREAM] Tool result: {} ({} chars)", tool_name, len(str(content)))

        # Check for text content (final response tokens)
        # Only emit if there's actual content and it's a meaningful chunk
        if content and finish_reason == "stop":
            events.append(
                TextDeltaEvent(
                    timestamp=timestamp,
                    data={
                        "content": content,
                        "usage": {
                            "input_tokens": usage_metadata.get("input_tokens", 0),
                            "output_tokens": usage_metadata.get("output_tokens", 0),
                        },
                    },
                )
            )
            logger.debug("[STREAM] Text delta: {} chars", len(content))

        # Log chunk position for debugging
        if chunk_position:
            logger.debug("[STREAM] Chunk position: {}", chunk_position)

        return events

    async def stream(
        self, input_text: str, thread_id: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        """
        Stream agent execution with event conversion.

        Uses stream_mode="messages" to get token-level streaming with:
        - TextDeltaEvent: Token-level text content
        - ReasoningEvent: Model reasoning/thinking
        - ToolCallEvent: Tool invocation
        - ToolResultEvent: Tool execution result

        Args:
            input_text: User input question or task
            thread_id: Optional thread ID for conversation persistence

        Yields:
            AgentEvent instances representing execution steps

        Raises:
            Exception: If agent execution fails
        """
        logger.info("Starting agent execution with input: {}", input_text[:100])

        try:
            # Import here to avoid circular imports
            from deepagents import create_deep_agent

            # Create agent with optional sandbox backend
            if self._sandbox:
                logger.info("Creating agent with sandbox backend: {}", self._sandbox.id)
                # Load skills configuration
                from cubebox.config import config

                skills_sources = config.get("sandbox.skills.sources", ["/.skills/builtin"])
                logger.info("Creating agent with skills: {}", skills_sources)
                agent = create_deep_agent(
                    model=self.llm,
                    tools=self.tools,
                    backend=self._sandbox,
                    skills=skills_sources,
                    checkpointer=self.checkpointer,
                )
            else:
                logger.info("Creating agent without sandbox")
                agent = create_deep_agent(
                    model=self.llm, tools=self.tools, checkpointer=self.checkpointer
                )
            logger.debug("Agent created successfully")

            # Yield chain start event
            yield ChainStartEvent(
                timestamp=self._get_current_timestamp(),
                data={"input": input_text},
            )

            # Stream events using messages mode for token-level streaming
            chunk_count = 0
            config_dict: dict[str, object] = (
                {"configurable": {"thread_id": thread_id}} if thread_id else {}
            )

            # Use stream_mode="messages" for token-level streaming
            async for chunk in agent.astream(
                {"messages": [{"role": "user", "content": input_text}]},
                stream_mode="messages",
                config=config_dict,  # type: ignore[arg-type]
            ):
                chunk_count += 1
                logger.debug("[STREAM] Raw chunk #{}: {}", chunk_count, chunk)

                # Handle the chunk and yield any events
                for event in self._handle_stream_chunk(chunk):
                    yield event

            logger.info("Agent execution completed with {} chunks", chunk_count)

            # Yield done event
            yield DoneEvent(timestamp=self._get_current_timestamp())

        except Exception as e:
            logger.exception("Error during agent execution: {}", str(e), exc_info=True)
            # Yield error event
            yield ErrorEvent(
                timestamp=self._get_current_timestamp(),
                data={
                    "error_code": "EXECUTION_ERROR",
                    "message": "Agent execution failed",
                    "details": str(e),
                },
            )
            # Yield done event even on error
            yield DoneEvent(timestamp=self._get_current_timestamp())
