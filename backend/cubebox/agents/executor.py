"""DeepAgentExecutor

Core executor for running DeepAgent-based tasks with streaming support.
Handles agent creation, tool loading, and event streaming.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from langchain_core.tools import StructuredTool
from loguru import logger

from cubebox.agents.schemas import (
    AgentEvent,
    ChainStartEvent,
    DoneEvent,
    ErrorEvent,
    LLMEndEvent,
    ToolEndEvent,
    ToolStartEvent,
)
from cubebox.llm.factory import LLMFactory
from cubebox.tools import get_registry


class DeepAgentExecutor:
    """Executor for DeepAgent-based task execution with streaming support"""

    def __init__(
        self, *, sandbox_domain: str | None = None, sandbox_image: str | None = None
    ) -> None:
        """
        Initialize the DeepAgentExecutor.

        Creates LLM instance, loads tools from registry, and optionally creates sandbox.

        Args:
            sandbox_domain: OpenSandbox server domain (e.g., "localhost:8090")
                          If None, reads from config.sandbox.domain
            sandbox_image: Docker image for sandbox (e.g., "ubuntu:22.04")
                         If None, reads from config.sandbox.image
        """
        from cubebox.config import config

        logger.info("Initializing DeepAgentExecutor")
        self.llm = self._create_llm()
        self.tools = self._load_tools()

        # Use provided values or fall back to config
        # Only use config if sandbox is enabled
        sandbox_enabled = config.get("sandbox.enabled", False)
        if sandbox_enabled:
            self.sandbox_domain = sandbox_domain or config.get("sandbox.domain")
            self.sandbox_image = sandbox_image or config.get("sandbox.image")
            self.sandbox_api_key = config.get("sandbox.api_key")
        else:
            # If sandbox not enabled in config, only use explicitly provided values
            self.sandbox_domain = sandbox_domain
            self.sandbox_image = sandbox_image
            self.sandbox_api_key = None

        self._sandbox: Any | None = None
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

    def _load_tools(self) -> list[StructuredTool]:
        """
        Load tools from the tool registry.

        Returns:
            List of StructuredTool instances
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

    async def _create_sandbox(self) -> Any:
        """
        Create OpenSandbox instance if configured.

        Returns:
            OpenSandbox backend instance or None if not configured

        Raises:
            Exception: If sandbox creation fails
        """
        if not self.sandbox_domain or not self.sandbox_image:
            logger.info("Sandbox not configured, skipping sandbox creation")
            return None

        try:
            from datetime import timedelta

            import opensandbox
            from opensandbox.config import ConnectionConfig

            from cubebox.sandbox.opensandbox import OpenSandbox

            logger.info(
                "Creating OpenSandbox with domain: {}, image: {}",
                self.sandbox_domain,
                self.sandbox_image,
            )

            # Create connection config
            config = ConnectionConfig(
                domain=self.sandbox_domain,
                api_key=self.sandbox_api_key,
                request_timeout=timedelta(seconds=60),
            )

            # Create sandbox instance
            sandbox = await opensandbox.Sandbox.create(
                self.sandbox_image,
                connection_config=config,
                timeout=timedelta(minutes=10),
            )

            # Wrap with DeepAgents backend
            backend = OpenSandbox(sandbox=sandbox)
            logger.info("OpenSandbox created successfully: {}", backend.id)
            return backend

        except Exception as e:
            logger.error("Failed to create sandbox: {}", str(e))
            raise

    def _get_current_timestamp(self) -> str:
        """
        Get current timestamp in ISO 8601 format.

        Returns:
            ISO 8601 formatted timestamp string
        """
        return datetime.now(UTC).isoformat()

    def _convert_event(self, node_name: str, data: Any) -> AgentEvent | None:
        """
        Convert DeepAgent node update to AgentEvent.

        Args:
            node_name: DeepAgent node name (e.g., 'model', 'tools')
            data: Node update data

        Returns:
            AgentEvent instance or None if node type not relevant for streaming
        """
        timestamp = self._get_current_timestamp()

        try:
            # Handle model node (LLM calls)
            if node_name == "model":
                messages = data.get("messages", [])
                if not messages:
                    return None

                # Get the last message (most recent AI response)
                last_msg = messages[-1]

                # Check if this is a tool call or final response
                tool_calls = getattr(last_msg, "tool_calls", [])
                content = getattr(last_msg, "content", "")
                usage_metadata = getattr(last_msg, "usage_metadata", {})

                if tool_calls:
                    # This is a tool call - yield tool start events
                    # We'll return the first tool call as an event
                    # (multiple tool calls would need multiple events)
                    tool_call = tool_calls[0]
                    return ToolStartEvent(
                        timestamp=timestamp,
                        data={
                            "tool_name": tool_call.get("name", "unknown"),
                            "input": tool_call.get("args", {}),
                        },
                    )
                elif content:
                    # This is a final response
                    return LLMEndEvent(
                        timestamp=timestamp,
                        data={
                            "output": content,
                            "usage": {
                                "input_tokens": usage_metadata.get("input_tokens", 0),
                                "output_tokens": usage_metadata.get("output_tokens", 0),
                            },
                        },
                    )
                else:
                    # No tool calls and no content - ignore this update
                    return None

            # Handle tools node (tool execution results)
            elif node_name == "tools":
                messages = data.get("messages", [])
                if not messages:
                    return None

                # Get the last tool message
                last_msg = messages[-1]
                tool_name = getattr(last_msg, "name", "unknown")
                content = getattr(last_msg, "content", "")

                return ToolEndEvent(
                    timestamp=timestamp,
                    data={
                        "tool_name": tool_name,
                        "output": content,
                    },
                )

            # Ignore middleware nodes and other internal nodes
            else:
                return None

        except Exception as e:
            logger.error("Error converting node {} update: {}", node_name, str(e))
            return None

    async def stream(self, input_text: str) -> AsyncIterator[AgentEvent]:
        """
        Stream agent execution with event conversion.

        Executes the agent with the given input and yields AgentEvent instances
        for each step in the execution.

        Args:
            input_text: User input question or task

        Yields:
            AgentEvent instances representing execution steps

        Raises:
            Exception: If agent execution fails
        """
        logger.info("Starting agent execution with input: {}", input_text[:100])

        try:
            # Import here to avoid circular imports
            from deepagents import create_deep_agent

            # Create sandbox if configured and not already created
            if self.sandbox_domain and self.sandbox_image and not self._sandbox:
                self._sandbox = await self._create_sandbox()

            # Create agent with optional sandbox backend
            if self._sandbox:
                logger.info("Creating agent with sandbox backend: {}", self._sandbox.id)
                agent = create_deep_agent(model=self.llm, tools=self.tools, backend=self._sandbox)
            else:
                logger.info("Creating agent without sandbox")
                agent = create_deep_agent(model=self.llm, tools=self.tools)
            logger.debug("Agent created successfully")

            # Yield chain start event
            yield ChainStartEvent(
                timestamp=self._get_current_timestamp(),
                data={"input": input_text},
            )

            # Stream events from agent
            event_count = 0
            async for chunk in agent.astream(
                {"messages": [{"role": "user", "content": input_text}]},
                stream_mode="updates",
            ):
                event_count += 1
                logger.debug("Received chunk from node: {}", list(chunk.keys()) if chunk else [])

                # Convert and yield event
                # Note: chunk is a dict with node_name -> data mapping
                if isinstance(chunk, dict):
                    for node_name, data in chunk.items():
                        converted_event = self._convert_event(node_name, data)
                        if converted_event:
                            logger.debug("Yielding event: {}", converted_event.type)
                            yield converted_event

            logger.info("Agent execution completed with {} chunks", event_count)

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
        finally:
            # Cleanup sandbox if created
            if self._sandbox:
                try:
                    logger.info("Cleaning up sandbox: {}", self._sandbox.id)
                    # Get the underlying opensandbox instance and kill it
                    await self._sandbox._sandbox.kill()
                    self._sandbox = None
                    logger.info("Sandbox cleaned up successfully")
                except Exception as e:
                    logger.error("Failed to cleanup sandbox: {}", str(e))
