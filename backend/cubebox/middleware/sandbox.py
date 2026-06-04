"""SandboxMiddleware.

Implements the cubepi ``Middleware`` protocol with two hooks:

- ``tools``: exposes ``execute``, ``write_file``, ``edit_file``, and
  ``file_read`` as ``cubepi.AgentTool`` instances.
- ``transform_system_prompt``: appends the sandbox capability section
  (SANDBOX_PROMPT_TEMPLATE) to the system prompt.

Audit helpers (``enable_audit``, ``disable_audit``, ``executed_commands``,
``reset_executed_commands``, ``_record_executed``) live in module-global
state so existing E2E fixtures that call ``enable_audit()`` can observe
command execution.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from cubepi.agent.types import AgentTool, AgentToolResult, BeforeToolCallResult
from cubepi.hitl import HitlCancelled, HitlChannel, HitlTimedOut
from cubepi.middleware.base import Middleware
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubebox.parsers import ParseOptions
from cubebox.prompts.sandbox import SANDBOX_PROMPT_TEMPLATE
from cubebox.sandbox.base import Sandbox
from cubebox.sandbox_policy.rules import evaluate_command

# ---------------------------------------------------------------------------
# Per-(workspace_id, conversation_id) ring buffer of commands the sandbox
# actually ran (exit_code == 0). Disabled by default: production workers
# would otherwise grow one deque per conversation forever (no consumer
# evicts entries). E2E tests opt in via enable_audit() in a fixture; the
# fixture also calls reset_executed_commands() on teardown so state does
# not leak across tests.
# ---------------------------------------------------------------------------

_EXECUTED_COMMANDS: dict[tuple[str, str], deque[str]] = {}
_EXECUTED_COMMANDS_CAP = 50
_AUDIT_ENABLED = False


def enable_audit() -> None:
    """Enable command-audit recording. Tests call this from a fixture."""
    global _AUDIT_ENABLED
    _AUDIT_ENABLED = True


def disable_audit() -> None:
    """Disable recording and clear any accumulated state."""
    global _AUDIT_ENABLED
    _AUDIT_ENABLED = False
    _EXECUTED_COMMANDS.clear()


def _record_executed(workspace_id: str, conversation_id: str, command: str) -> None:
    if not _AUDIT_ENABLED:
        return
    key = (workspace_id, conversation_id)
    buf = _EXECUTED_COMMANDS.get(key)
    if buf is None:
        buf = deque(maxlen=_EXECUTED_COMMANDS_CAP)
        _EXECUTED_COMMANDS[key] = buf
    buf.append(command)


def executed_commands(workspace_id: str, conversation_id: str) -> list[str]:
    """Last <=50 commands the sandbox actually ran (exit_code == 0).

    Returns the empty list unless ``enable_audit()`` was called (typically
    from a test fixture). Sandbox-rejected attempts (non-zero exit) are
    intentionally NOT recorded; semantics are "what hit the filesystem",
    not "what the LLM tried".
    """
    return list(_EXECUTED_COMMANDS.get((workspace_id, conversation_id), ()))


def reset_executed_commands() -> None:
    """Clear all recorded commands. Test helper."""
    _EXECUTED_COMMANDS.clear()


# ---------------------------------------------------------------------------
# Input schemas for sandbox tools
# ---------------------------------------------------------------------------


class _ExecuteArgs(BaseModel):
    command: str


class _WriteFileArgs(BaseModel):
    file_path: str = Field(description="Absolute path where the file should be created.")
    content: str = Field(description="The text content to write to the file.")


class _EditFileArgs(BaseModel):
    file_path: str = Field(description="Absolute path to the file to edit.")
    old_string: str = Field(description="The exact text to find and replace. Must be unique.")
    new_string: str = Field(description="The replacement text. Must differ from old_string.")


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


# ---------------------------------------------------------------------------
# Tool factories
# ---------------------------------------------------------------------------


def _make_execute_tool(
    sandbox: Sandbox,
    *,
    workspace_id: str | None = None,
    conversation_id: str | None = None,
) -> AgentTool[_ExecuteArgs]:
    """Build the execute cubepi.AgentTool backed by a sandbox instance.

    Command-policy rules (deny / confirm) are enforced one layer up, in
    ``SandboxMiddleware.before_tool_call`` — the tool body itself is a pure
    executor.
    """

    async def _execute(
        tool_call_id: str,
        args: _ExecuteArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        result = await sandbox.execute(args.command)
        if workspace_id is not None and conversation_id is not None and result.exit_code == 0:
            _record_executed(workspace_id, conversation_id, args.command)
        output = result.output
        if result.exit_code is not None and result.exit_code != 0:
            output += f"\n[exit code: {result.exit_code}]"
        return AgentToolResult(content=[TextContent(text=output)])

    return AgentTool(
        name="execute",
        description="Execute a shell command in the sandbox environment.",
        parameters=_ExecuteArgs,
        execute=_execute,
    )


def _make_write_file_tool(sandbox: Sandbox) -> AgentTool[_WriteFileArgs]:
    """Build the write_file cubepi.AgentTool backed by a sandbox instance."""

    async def _write_file(
        tool_call_id: str,
        args: _WriteFileArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        await sandbox.upload([(args.file_path, args.content.encode())])
        return AgentToolResult(content=[TextContent(text=f"Successfully wrote {args.file_path}")])

    return AgentTool(
        name="write_file",
        description="Create or overwrite a file with the given content.",
        parameters=_WriteFileArgs,
        execute=_write_file,
    )


def _make_edit_file_tool(sandbox: Sandbox) -> AgentTool[_EditFileArgs]:
    """Build the edit_file cubepi.AgentTool backed by a sandbox instance."""

    async def _edit_file(
        tool_call_id: str,
        args: _EditFileArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        if args.old_string == args.new_string:
            return AgentToolResult(
                content=[TextContent(text="Error: old_string and new_string must differ.")]
            )
        try:
            files = await sandbox.download([args.file_path])
        except FileNotFoundError:
            return AgentToolResult(
                content=[TextContent(text=f"Error: file not found — {args.file_path}")]
            )
        except Exception as exc:
            return AgentToolResult(
                content=[TextContent(text=f"Error reading {args.file_path}: {exc}")]
            )
        current = files[0][1].decode()
        count = current.count(args.old_string)
        if count == 0:
            return AgentToolResult(
                content=[TextContent(text=f"Error: old_string not found in {args.file_path}")]
            )
        if count > 1:
            return AgentToolResult(
                content=[
                    TextContent(
                        text=(
                            f"Error: old_string appears {count} times in {args.file_path}. "
                            "It must be unique — provide more context."
                        )
                    )
                ]
            )
        updated = current.replace(args.old_string, args.new_string, 1)
        await sandbox.upload([(args.file_path, updated.encode())])
        return AgentToolResult(content=[TextContent(text=f"Successfully edited {args.file_path}")])

    return AgentTool(
        name="edit_file",
        description="Find and replace a unique string in an existing file.",
        parameters=_EditFileArgs,
        execute=_edit_file,
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


def _make_file_read_tool(
    sandbox: Sandbox,
    conversation_id: str | None,
) -> AgentTool[_FileReadArgs]:
    """Build the file_read cubepi.AgentTool backed by a sandbox + (optional) conversation."""

    async def _file_read(
        tool_call_id: str,
        args: _FileReadArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        import json

        del tool_call_id, signal, on_update

        result = await sandbox.file_read(
            args.path,
            options=ParseOptions(page_range=args.page_range, line_range=args.line_range),
            conversation_id=conversation_id,
        )
        return AgentToolResult(content=[TextContent(text=json.dumps(result.model_dump()))])

    return AgentTool(
        name="file_read",
        description=_FILE_READ_DESCRIPTION.rstrip(),
        parameters=_FileReadArgs,
        execute=_file_read,
    )


# ---------------------------------------------------------------------------
# Middleware class
# ---------------------------------------------------------------------------


class SandboxMiddleware(Middleware):
    """Registers sandbox tools and injects sandbox capability section into system prompt.

    Usage::

        mw = SandboxMiddleware(
            sandbox=sandbox,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        # collect mw.tools and pass to Agent(tools=[...])
        # register mw with Agent(middleware=[mw]) for transform_system_prompt

    The audit helpers from ``sandbox.py`` (``enable_audit`` / ``disable_audit`` /
    ``executed_commands`` / ``reset_executed_commands``) remain shared so that
    existing E2E test fixtures work without modification.
    """

    def __init__(
        self,
        *,
        sandbox: Sandbox,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        command_rules: list[dict[str, Any]] | None = None,
        channel: HitlChannel | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.conversation_id = conversation_id
        self.workspace_id = workspace_id
        self.command_rules = command_rules or []
        self.channel = channel

        self._tools: list[AgentTool[Any]] = [
            _make_execute_tool(
                sandbox,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
            ),
            _make_write_file_tool(sandbox),
            _make_edit_file_tool(sandbox),
            _make_file_read_tool(sandbox, conversation_id),
        ]

    @property
    def tools(self) -> list[AgentTool[Any]]:
        """Return the cubepi.AgentTool list for this middleware."""
        return list(self._tools)

    async def before_tool_call(
        self,
        ctx: Any,
        *,
        signal: asyncio.Event | None = None,
    ) -> BeforeToolCallResult | None:
        """Enforce command rules before the execute tool runs.

        v1: execute only. deny → block; confirm → pause on the HITL channel
        (approve runs it, deny/timeout/cancel block it); edit is rejected.
        Because this runs before the tool body, a blocked command never reaches
        ``sandbox.execute`` — no sandbox side effects, TTL clock untouched.
        """
        if getattr(ctx.tool_call, "name", None) != "execute":
            return None
        if not self.command_rules:
            return None

        command = ctx.args.command
        action, pattern = evaluate_command(command, self.command_rules)
        if action == "allow":
            return None
        if action == "deny":
            return BeforeToolCallResult(
                block=True,
                reason=f"command blocked by org policy: {pattern}",
                deny_reason=pattern,
                hitl_trace={"decision": "policy_deny", "pattern": pattern},
            )

        # action == "confirm": fail-closed if no channel is wired for this run.
        if self.channel is None:
            return BeforeToolCallResult(
                block=True,
                reason="approval required but HITL channel is unavailable",
                deny_reason="hitl_unavailable",
                hitl_trace={"decision": "hitl_unavailable", "pattern": pattern},
            )

        try:
            answer = await self.channel.approve(
                tool_name="execute",
                tool_call_id=ctx.tool_call.id,
                args={"command": command},
                details={"matched_pattern": pattern, "command": command},
                signal=signal,
            )
        except HitlTimedOut:
            return BeforeToolCallResult(
                block=True,
                reason="approval timed out (180s); command not run",
                deny_reason="approval_timeout",
                hitl_trace={"decision": "timed_out"},
            )
        except HitlCancelled as exc:
            return BeforeToolCallResult(
                block=True,
                reason=f"cancelled: {exc.reason}",
                deny_reason=f"cancelled: {exc.reason}",
                hitl_trace={"decision": "cancelled", "reason": exc.reason},
            )

        if answer.decision == "approve":
            return None
        if answer.decision == "deny":
            return BeforeToolCallResult(
                block=True,
                reason=answer.reason or "denied by user",
                deny_reason=answer.reason or "denied by user",
                hitl_trace={"decision": "human_deny", "reason": answer.reason},
            )
        raise ValueError("edit decision not supported for sandbox confirm v1")

    async def transform_system_prompt(
        self,
        system_prompt: str,
        *,
        ctx: object,
        signal: object = None,
    ) -> str:
        """Append sandbox capability section to the system prompt.

        Mirrors ``SandboxMiddleware.awrap_model_call`` which called
        ``append_to_system_message(request.system_message, prompt)``.

        Idempotent: calling with identical inputs always yields identical
        output — the capability section is appended unconditionally so
        the output is deterministic and cache-stable.
        """
        del ctx, signal  # not used

        sandbox_section = SANDBOX_PROMPT_TEMPLATE.format(workdir=self.sandbox.workdir)
        separator = "\n\n" if system_prompt else ""
        return system_prompt + separator + sandbox_section
