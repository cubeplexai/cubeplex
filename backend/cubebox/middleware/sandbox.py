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
from pydantic import BaseModel, Field

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


class _WriteFileArgs(BaseModel):
    file_path: str = Field(description="Absolute path where the file should be created.")
    content: str = Field(description="The text content to write to the file.")


class _EditFileArgs(BaseModel):
    file_path: str = Field(description="Absolute path to the file to edit.")
    old_string: str = Field(description="The exact text to find and replace. Must be unique.")
    new_string: str = Field(description="The replacement text. Must differ from old_string.")


def _create_write_file_tool(sandbox: Sandbox) -> BaseTool:
    """Build the write_file tool backed by a sandbox instance."""

    async def _write_file(file_path: str, content: str) -> str:
        await sandbox.upload([(file_path, content.encode())])
        return f"Successfully wrote {file_path}"

    return StructuredTool.from_function(
        coroutine=_write_file,
        name="write_file",
        description="Create or overwrite a file with the given content.",
        args_schema=_WriteFileArgs,
    )


def _create_edit_file_tool(sandbox: Sandbox) -> BaseTool:
    """Build the edit_file tool backed by a sandbox instance."""

    async def _edit_file(file_path: str, old_string: str, new_string: str) -> str:
        if old_string == new_string:
            return "Error: old_string and new_string must differ."
        files = await sandbox.download([file_path])
        current = files[0][1].decode()
        count = current.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {file_path}"
        if count > 1:
            return (
                f"Error: old_string appears {count} times in {file_path}. "
                "It must be unique — provide more context."
            )
        updated = current.replace(old_string, new_string, 1)
        await sandbox.upload([(file_path, updated.encode())])
        return f"Successfully edited {file_path}"

    return StructuredTool.from_function(
        coroutine=_edit_file,
        name="edit_file",
        description="Find and replace a unique string in an existing file.",
        args_schema=_EditFileArgs,
    )


class SandboxMiddleware(AgentMiddleware[Any, Any, Any]):
    """Registers the execute tool and injects sandbox context into system prompt."""

    def __init__(self, *, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        self.tools: Sequence[BaseTool] = [
            _create_execute_tool(sandbox),
            _create_write_file_tool(sandbox),
            _create_edit_file_tool(sandbox),
        ]

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        prompt = SANDBOX_PROMPT_TEMPLATE.format(workdir=self.sandbox.workdir)
        new_system = append_to_system_message(request.system_message, prompt)
        return await handler(request.override(system_message=new_system))
