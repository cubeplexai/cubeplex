"""SandboxMiddleware — registers the execute tool and injects sandbox context."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from cubebox.middleware._utils import append_to_system_message
from cubebox.prompts.sandbox import SANDBOX_PROMPT_TEMPLATE
from cubebox.sandbox.base import Sandbox


class _ExecuteArgs(BaseModel):
    command: str


def _create_execute_tool(sandbox: Sandbox) -> BaseTool:
    """Build the execute tool backed by a sandbox instance."""

    async def _execute(command: str) -> str:
        result = await sandbox.execute(command)
        output = result.output
        if result.exit_code is not None and result.exit_code != 0:
            output += f"\n[exit code: {result.exit_code}]"
        return output

    return StructuredTool.from_function(
        coroutine=_execute,
        name="execute",
        description="Execute a shell command in the sandbox environment.",
        args_schema=_ExecuteArgs,
    )


class SandboxMiddleware(AgentMiddleware[Any, Any, Any]):
    """Registers the execute tool and injects sandbox context into system prompt."""

    def __init__(self, *, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        self.tools: Sequence[BaseTool] = [_create_execute_tool(sandbox)]

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        prompt = SANDBOX_PROMPT_TEMPLATE.format(workdir=self.sandbox.workdir)
        new_system = append_to_system_message(request.system_message, prompt)
        return await handler(request.override(system_message=new_system))
