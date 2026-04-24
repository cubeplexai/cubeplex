"""SandboxMiddleware — registers the execute tool and injects sandbox context."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from uuid import UUID

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from cubebox.middleware._utils import append_to_system_message
from cubebox.parsers import ParseOptions
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
        try:
            files = await sandbox.download([file_path])
        except FileNotFoundError:
            return f"Error: file not found — {file_path}"
        except Exception as exc:
            return f"Error reading {file_path}: {exc}"
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


class _FileReadArgs(BaseModel):
    path: str = Field(description="Absolute path inside the sandbox to the file to read.")
    page_range: str | None = Field(
        default=None,
        description=(
            "Optional 1-indexed page range, e.g. '1-5' or '3'. "
            "Paginated documents only: PDF / DOCX / PPTX."
        ),
    )
    line_range: str | None = Field(
        default=None,
        description=(
            "Optional 1-indexed line range, e.g. '100-200' or '42'. "
            "Text / code / log files only. Lets you navigate large text files "
            "(e.g. 100k-line logs) by line number."
        ),
    )


_FILE_READ_DESCRIPTION = """\
Read a file from the sandbox workspace and return its content in a form
you can reason about. Use this whenever you need to inspect user uploads,
agent-generated artifacts, or any file inside the sandbox — not shell
output, not network resources.

USE THIS TOOL FOR:
- Text / source code (.txt .md .py .js .ts .json .yaml .toml .csv .html
  .css .go .rs .java .cpp etc.) — returns raw UTF-8 text.
- Documents (.pdf .docx .pptx .xlsx .epub) — returns markdown
  preserving headings, tables, lists.
- Notebooks (.ipynb) — returns structured cells.
- Images (.png .jpg .webp .tiff) — returns OCR'd text content.

WHEN OTHER TOOLS ARE BETTER:
- Remote URLs — file_read only reads sandbox paths. Use a web-fetch
  tool for URLs.
- Grep / search — for pattern-find, execute("grep -n 'pattern' <file>")
  is more direct than file_read + scan.
- Tiny known-offset peeks — execute("sed -n '42p' <file>") skips
  parser overhead.

HOW UNSUPPORTED FORMATS BEHAVE:
- The tool returns kind="unsupported" with a `hint` when no parser
  plugin handles the file's MIME type. Common cases — video, audio,
  archives, binary executables — fall here in the default deployment.
- The `hint` field tells you what alternative to try (e.g., for
  archives: extract first via execute("unzip <file>") then file_read
  on extracted files).
- If you see kind="unsupported", surface the hint to the user; don't
  retry file_read on the same path.

RETURN FORMAT (discriminated by `kind`):
- "text"        : {content, mime, size_bytes, truncated, metadata}
- "notebook"    : {cells: [{cell_type, source, outputs}, ...]}
- "unsupported" : {reason, hint, mime, size_bytes}
- "unchanged"   : file unchanged since previous file_read in this session
- "error"       : {error, retryable}

PARAMETERS:
- path (required)         — absolute sandbox path
- page_range (optional)   — paginated documents only: PDF/DOCX/PPTX
- line_range (optional)   — text/code/log files only

RANGE SYNTAX (page_range and line_range share these 4 forms):
  "42"      — single line/page (item 42)
  "100-200" — range from 100 to 200 inclusive
  "100-"    — from 100 to end of file (sed '100,$' style)
  "-50"     — last 50 lines/pages (tail -50 style)

HOW TO CONTINUE READING WHEN truncated=true:
- text/code/log: read metadata.next_line_to_read and call
  file_read(path, line_range=f"{N}-") to continue from there.
- PDF/DOCX/PPTX (best-effort): read metadata.next_page_to_read
  and call file_read(path, page_range=f"{N}-"). If the field
  is absent (parser couldn't map char-offset back to page),
  fall back to ranges you guess or ask the user.
- notebook: metadata.next_cell_index is informational only —
  v1 has no cell_range param. The first batch is what you get.

LIMITS:
- Files > 100 MB are refused with kind="unsupported".
- Content longer than 20,000 characters is truncated. See
  "HOW TO CONTINUE READING" above.
- Large files (>3 MB) trigger async parsing; up to 10 minutes.
"""


def _create_file_read_tool(sandbox: Sandbox, conversation_id: UUID | None) -> BaseTool:
    """Build the file_read tool backed by a sandbox + (optional) conversation."""

    async def _file_read(
        path: str,
        page_range: str | None = None,
        line_range: str | None = None,
    ) -> dict[str, Any]:
        result = await sandbox.file_read(
            path,
            options=ParseOptions(page_range=page_range, line_range=line_range),
            conversation_id=conversation_id,
        )
        return result.model_dump()

    return StructuredTool.from_function(
        coroutine=_file_read,
        name="file_read",
        description=_FILE_READ_DESCRIPTION,
        args_schema=_FileReadArgs,
        metadata={"content_type": "file_read"},
    )


def _coerce_uuid(value: str | UUID | None) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


class SandboxMiddleware(AgentMiddleware[Any, Any, Any]):
    """Registers the execute tool and injects sandbox context into system prompt."""

    def __init__(
        self,
        *,
        sandbox: Sandbox,
        conversation_id: str | UUID | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.conversation_id = _coerce_uuid(conversation_id)
        self.tools: Sequence[BaseTool] = [
            _create_execute_tool(sandbox),
            _create_write_file_tool(sandbox),
            _create_edit_file_tool(sandbox),
            _create_file_read_tool(sandbox, self.conversation_id),
        ]

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        prompt = SANDBOX_PROMPT_TEMPLATE.format(workdir=self.sandbox.workdir)
        new_system = append_to_system_message(request.system_message, prompt)
        return await handler(request.override(system_message=new_system))
