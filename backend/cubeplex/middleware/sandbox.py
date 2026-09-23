"""SandboxMiddleware.

Implements the cubeloop ``Middleware`` protocol with two hooks:

- ``tools``: exposes ``execute``, ``write``, ``edit``,
  ``read``, and (when a config loader is provided) ``sandbox_config``
  as ``cubeloop.AgentTool`` instances.
- ``transform_system_prompt``: appends the sandbox capability section
  (SANDBOX_PROMPT_TEMPLATE) to the system prompt.

Audit helpers (``enable_audit``, ``disable_audit``, ``executed_commands``,
``reset_executed_commands``, ``_record_executed``) live in module-global
state so existing E2E fixtures that call ``enable_audit()`` can observe
command execution.
"""

from __future__ import annotations

import asyncio
import difflib
import inspect
import re
import shlex
import time
import unicodedata
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from cubeloop.agent.types import (
    AgentContext,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
)
from cubeloop.hitl import HitlCancelled, HitlChannel, HitlTimedOut
from cubeloop.middleware.base import Middleware
from cubeloop.providers.base import (
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from cubeloop.types import StructuredValue
from loguru import logger
from pydantic import BaseModel, Field, model_validator

from cubeplex.config import MAX_COMMAND_TIMEOUT_SECONDS, get_command_default_timeout_seconds
from cubeplex.models.background_task import TaskStopReason
from cubeplex.models.public_id import PREFIX_SANDBOX_COMMAND, generate_public_id
from cubeplex.parsers import ParseOptions
from cubeplex.prompts.sandbox import SANDBOX_PROMPT_TEMPLATE
from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot, Sandbox
from cubeplex.sandbox.log_io import AppendOutputResult, append_output
from cubeplex.sandbox_policy.rules import evaluate_command
from cubeplex.services.background_task_lifecycle import LogState
from cubeplex.services.sandbox_runtime_config import POLICY_DENY_NUDGE
from cubeplex.tools.builtin.sandbox_config import (
    SandboxConfigLoader,
    create_sandbox_config_tool,
)
from cubeplex.utils.time import utc_isoformat

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


# Agent-facing default. Drivers must honor this so a hung `gh` / network
# call becomes a tool result the model can retry from, not a silent stall.
# Match ToolResultLimitMiddleware so we spill before that rewrite.
EXECUTE_RESULT_SPILL_CHARS = 20_000
_EXECUTE_UPDATE_INTERVAL_SECONDS = 0.1
_BACKGROUND_POLL_INTERVAL_SECONDS = 1.0
MAX_LIVE_BACKGROUND_COMMANDS = 8
_COMMAND_LEASE_SECONDS = 15
_TASK_OWNER_LEASE_SECONDS = 45
AUTO_BACKGROUND_SECONDS = 15
_BARE_SLEEP_RE = re.compile(r"^sleep(\s+\S+)?\s*$")


class _AutoBackgroundUnavailable(Exception):
    """The command must use the foreground path because background slots are full."""


@dataclass(frozen=True)
class _ReservedCommand:
    command_id: str
    task_id: str
    start_allowed: bool = True
    log_path: str | None = None
    deadline_at: datetime | None = None


@dataclass(frozen=True)
class _TaskCommandBinding:
    task_id: str
    owner_token: str
    sandbox_instance_id: str


def _bounded_execute_excerpt(text: str, *, suffix: str = "") -> str:
    """Head+tail excerpt that still fits in ToolResultLimitMiddleware.

    ``suffix`` (spill path, truncated marker) is included in the 20k budget
    so after_tool_call cannot strip the tail or the path.
    """
    budget = EXECUTE_RESULT_SPILL_CHARS - len(suffix)
    if budget < 64:
        budget = 64
    if len(text) <= budget:
        return text + suffix
    omitted = len(text)
    marker = f"\n\n[... {omitted} chars omitted ...]\n\n"
    keep_total = budget - len(marker)
    if keep_total < 2:
        return (text[:budget] + suffix)[:EXECUTE_RESULT_SPILL_CHARS]
    keep = keep_total // 2
    omitted = len(text) - 2 * keep
    marker = f"\n\n[... {omitted} chars omitted ...]\n\n"
    body = f"{text[:keep]}{marker}{text[-keep:]}"
    out = body + suffix
    if len(out) > EXECUTE_RESULT_SPILL_CHARS:
        return out[:EXECUTE_RESULT_SPILL_CHARS]
    return out


class _ExecuteArgs(BaseModel):
    description: str = Field(
        description=(
            "Short, user-facing summary of what this command does (5-10 words). "
            "Emit this FIRST, before command, so the chat UI can show it while "
            "the command text is still streaming."
        ),
    )
    command: str
    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=MAX_COMMAND_TIMEOUT_SECONDS,
        description=(
            "Seconds before the command is killed. The deployment default is one hour. "
            "Raise or lower it when the task has a different execution deadline."
        ),
    )
    background: bool = Field(
        default=False,
        description=(
            "If true, start the command and return a command_id immediately. "
            "You will be notified when it exits. Do not use shell &."
        ),
    )
    notify_on_complete: bool = Field(
        default=True,
        description="When background=true, inject a notice when the command exits.",
    )


class _WriteFileArgs(BaseModel):
    file_path: str = Field(description="Absolute path where the file should be created.")
    content: str = Field(description="The text content to write to the file.")
    overwrite: bool = Field(
        default=False,
        description=(
            "If false (default), refuse to overwrite an existing file — returns an "
            "error naming the existing file so you can pick a different name or set "
            "overwrite=true when you genuinely intend to replace it. Set "
            "overwrite=true only when clobbering an existing file is the explicit goal."
        ),
    )


class _EditSpec(BaseModel):
    old_string: str = Field(description="The exact text to find and replace. Must be unique.")
    new_string: str = Field(description="The replacement text. Must differ from old_string.")


class _EditFileArgs(BaseModel):
    file_path: str = Field(description="Absolute path to the file to edit.")
    edits: list[_EditSpec] | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "One or more edits to apply to the file in a single call. All old_string values "
            "must be unique in the original file and edits must not overlap."
        ),
    )
    old_string: str | None = Field(
        default=None,
        description="Legacy single-edit form; use edits instead when making multiple changes.",
    )
    new_string: str | None = Field(
        default=None,
        description="Legacy single-edit form; use edits instead when making multiple changes.",
    )

    @model_validator(mode="after")
    def validate_edit_shape(self) -> _EditFileArgs:
        if self.edits is None and (self.old_string is None or self.new_string is None):
            raise ValueError("Provide edits or both old_string and new_string.")
        if self.edits is not None and (self.old_string is not None or self.new_string is not None):
            raise ValueError("Provide edits or old_string/new_string, not both.")
        return self


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


def _shquote(path: str) -> str:
    """Shell-quote a path for safe embedding in an execute command."""
    return shlex.quote(path)


def _timeout_tool_message(seconds: int) -> str:
    return (
        f"[timeout] Command exceeded {seconds}s and was killed. "
        "Split the work or use a faster command, then retry."
    )


def _is_timeout_result(output: str, exit_code: int | None) -> bool:
    return exit_code == -1 and output.strip().startswith("[timeout]")


def _is_timeout_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "timeout" in text or "timed out" in text


_SHELL_BG_RE = re.compile(r"(^|\s)(nohup|disown)(\s|$)|&\s*$")


def _looks_like_shell_background(command: str) -> bool:
    return _SHELL_BG_RE.search(command.strip()) is not None


def _is_bare_sleep(command: str) -> bool:
    """True for `sleep` / `sleep 30`, not `sleep 5 && make`."""
    return _BARE_SLEEP_RE.match(command.strip()) is not None


async def _maybe_await(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


async def _write_sandbox_log(sandbox: Sandbox, path: str, data: bytes) -> AppendOutputResult:
    return await append_output(sandbox, path, data)


async def _append_sandbox_log(sandbox: Sandbox, path: str, text: str) -> AppendOutputResult:
    if not text:
        return AppendOutputResult(data_written=True, cleanup_done=True)
    return await append_output(sandbox, path, text)


def _make_execute_tool(
    sandbox: Sandbox,
    *,
    workspace_id: str | None = None,
    conversation_id: str | None = None,
    live: dict[str, tuple[ProcessHandle, bool]] | None = None,
    live_lock: asyncio.Lock | None = None,
    persist_reserve: Callable[..., Awaitable[bool | _ReservedCommand]] | None = None,
    persist_running: Callable[[str, str], Awaitable[None]] | None = None,
    persist_cursor: Callable[[str, str], Awaitable[None]] | None = None,
    persist_killed: Callable[[str], Awaitable[None]] | None = None,
    persist_start_failed: Callable[[str, str], Awaitable[None]] | None = None,
    persist_exited: Callable[[str, int | None, bool], Awaitable[None]] | None = None,
    persist_discard: Callable[[str], Awaitable[None]] | None = None,
    persist_foreground: Callable[[str, ProcessSnapshot, bool], Awaitable[None]] | None = None,
    persist_background_terminal: (
        Callable[[str, ProcessSnapshot, bool, bool], Awaitable[None]] | None
    ) = None,
    persist_timed_out: (
        Callable[[str, bool, ProcessSnapshot, bool], Awaitable[None]] | None
    ) = None,
    load_persisted_status: Callable[[str], Awaitable[str | None]] | None = None,
    deadline_tasks: dict[str, asyncio.Task[None]] | None = None,
    on_live: Callable[[], None] | None = None,
    handoff: Callable[[str], Awaitable[bool]] | None = None,
) -> AgentTool[_ExecuteArgs]:
    """Build the execute cubeloop.AgentTool backed by a sandbox instance.

    Command-policy rules (deny / confirm) are enforced one layer up, in
    ``SandboxMiddleware.before_tool_call`` — the tool body itself is a pure
    executor.
    """
    live_commands = live if live is not None else {}
    timeout_tasks = deadline_tasks if deadline_tasks is not None else {}
    lock = live_lock if live_lock is not None else asyncio.Lock()

    async def _ack_snapshot_output(
        command_id: str,
        handle: ProcessHandle,
        log_path: str,
        snapshot: ProcessSnapshot,
    ) -> bool:
        if snapshot.new_output or snapshot.status != "running":
            appended = await _append_sandbox_log(sandbox, log_path, snapshot.new_output)
            if not appended.data_written:
                return False
        if snapshot.log_cursor is not None:
            if persist_cursor is not None:
                await persist_cursor(command_id, snapshot.log_cursor)
            handle.log_cursor = snapshot.log_cursor
        return True

    async def _execute(
        tool_call_id: str,
        args: _ExecuteArgs,
        *,
        signal: asyncio.Event | None = None,
        on_update: Callable[[StructuredValue], None] | None = None,
    ) -> AgentToolResult:
        del signal

        timeout = args.timeout_seconds or get_command_default_timeout_seconds()
        background_timeout = timeout

        def _schedule_deadline(
            command_id: str,
            handle: ProcessHandle,
            log_path: str,
            *,
            kill_at: float,
        ) -> None:
            async def _enforce_deadline() -> None:
                await asyncio.sleep(max(0.0, kill_at - time.monotonic()))
                try:
                    async with lock:
                        current = live_commands.get(command_id)
                        if current is None or current[0] is not handle:
                            return
                        deadline_snapshot = await sandbox.poll(handle)
                        logs_confirmed = await _ack_snapshot_output(
                            command_id,
                            handle,
                            log_path,
                            deadline_snapshot,
                        )
                        if deadline_snapshot.status != "running":
                            live_commands.pop(command_id, None)
                            if persist_background_terminal is not None:
                                await persist_background_terminal(
                                    command_id,
                                    deadline_snapshot,
                                    logs_confirmed,
                                    current[1],
                                )
                            elif (
                                deadline_snapshot.status == "killed" and persist_killed is not None
                            ):
                                await persist_killed(command_id)
                            elif persist_exited is not None:
                                await persist_exited(
                                    command_id,
                                    deadline_snapshot.exit_code,
                                    current[1],
                                )
                            return
                        await sandbox.kill(handle)
                        confirmed = await sandbox.poll(handle)
                        logs_confirmed = await _ack_snapshot_output(
                            command_id,
                            handle,
                            log_path,
                            confirmed,
                        )
                        if confirmed.status == "running":
                            logger.warning(
                                "sandbox command {} still running after deadline interrupt",
                                command_id,
                            )
                            return
                        if persist_timed_out is not None:
                            await persist_timed_out(
                                command_id,
                                current[1],
                                confirmed,
                                logs_confirmed,
                            )
                except Exception:
                    logger.exception(
                        "failed to enforce sandbox command deadline {}",
                        command_id,
                    )

            task = asyncio.create_task(_enforce_deadline())
            timeout_tasks[command_id] = task
            task.add_done_callback(lambda _task: timeout_tasks.pop(command_id, None))

        if _looks_like_shell_background(args.command):
            return AgentToolResult(
                content=[
                    TextContent(
                        text=(
                            "Do not background with shell &, nohup, or disown. "
                            "Pass background=true instead."
                        )
                    )
                ],
                is_error=True,
            )
        if args.background:
            if not sandbox.supports_background():
                return AgentToolResult(
                    content=[TextContent(text="This sandbox cannot run background commands.")],
                    is_error=True,
                )
            command_id = generate_public_id(PREFIX_SANDBOX_COMMAND)
            log_path = f"{sandbox.workdir.rstrip('/')}/.cubeplex/execute-{command_id}.log"
            deadline_at = cast(
                datetime | None,
                datetime.now(UTC) + timedelta(seconds=background_timeout)
                if background_timeout is not None
                else None,
            )
            explicit_reservation: bool | _ReservedCommand = False
            explicit_task_id: str | None = None
            async with lock:
                if persist_reserve is not None:
                    try:
                        explicit_reservation = await persist_reserve(
                            command_id=command_id,
                            tool_call_id=tool_call_id,
                            command=args.command,
                            description=args.description,
                            notify_on_complete=args.notify_on_complete,
                            log_path=log_path,
                            timeout_seconds=background_timeout,
                            monitor_deadline_at=deadline_at,
                        )
                    except Exception as exc:
                        from cubeplex.repositories.sandbox_command import (
                            SandboxCommandCapError,
                        )

                        if isinstance(exc, SandboxCommandCapError):
                            return AgentToolResult(
                                content=[TextContent(text=str(exc))],
                                is_error=True,
                            )
                        logger.exception("sandbox command reserve failed")
                        return AgentToolResult(
                            content=[TextContent(text="failed to reserve background command")],
                            is_error=True,
                        )
                if isinstance(explicit_reservation, _ReservedCommand):
                    command_id = explicit_reservation.command_id
                    explicit_task_id = explicit_reservation.task_id
                    log_path = explicit_reservation.log_path or log_path
                    deadline_at = explicit_reservation.deadline_at
                    if not explicit_reservation.start_allowed:
                        return AgentToolResult(
                            content=[
                                TextContent(
                                    text=(
                                        f"Command is already managed in background as {command_id}."
                                    )
                                )
                            ],
                            details={
                                "status": "running",
                                "task_id": explicit_task_id,
                                "command_id": command_id,
                                "log_path": log_path,
                                "deadline_at": (
                                    utc_isoformat(deadline_at) if deadline_at is not None else None
                                ),
                                "notification": ("once" if args.notify_on_complete else "none"),
                                "result_pending": True,
                            },
                        )
                if not explicit_reservation and len(live_commands) >= MAX_LIVE_BACKGROUND_COMMANDS:
                    return AgentToolResult(
                        content=[TextContent(text="at most 8 running commands per sandbox")],
                        is_error=True,
                    )
                persist_error: BaseException | None = None

                async def _on_started(ref: str) -> None:
                    nonlocal persist_error
                    if persist_running is None:
                        return
                    try:
                        await persist_running(command_id, ref)
                    except Exception as exc:
                        persist_error = exc
                        logger.exception("sandbox command mark_running failed")

                try:
                    handle = await sandbox.start(
                        args.command,
                        timeout=background_timeout,
                        on_started=_on_started,
                    )
                except Exception:
                    if explicit_reservation and persist_start_failed is not None:
                        await persist_start_failed(command_id, "provider start failed")
                    elif explicit_reservation and persist_killed is not None:
                        await persist_killed(command_id)
                    raise
                handle.command_id = command_id
                handle.deadline_at = deadline_at
                if persist_error is not None:
                    await sandbox.kill(handle)
                    if persist_start_failed is not None:
                        await persist_start_failed(
                            command_id,
                            "provider start receipt persistence failed",
                        )
                    elif persist_killed is not None:
                        await persist_killed(command_id)
                    return AgentToolResult(
                        content=[TextContent(text="failed to persist background command")],
                        is_error=True,
                    )
                live_commands[command_id] = (handle, args.notify_on_complete)
            await _write_sandbox_log(sandbox, log_path, b"")
            handed_off = handoff is not None and await handoff(command_id)
            if not handed_off and on_live is not None:
                on_live()
            if not handed_off and background_timeout is not None:
                _schedule_deadline(
                    command_id,
                    handle,
                    log_path,
                    kill_at=time.monotonic() + background_timeout,
                )
            notice = (
                (
                    f"Task {explicit_task_id} is running as command {command_id}."
                    if explicit_task_id is not None
                    else f"Command running in background as {command_id}."
                )
                if args.notify_on_complete
                else f"Command {command_id} is running without a completion notice."
            )
            return AgentToolResult(
                content=[TextContent(text=notice)],
                details={
                    "status": "running",
                    **({"task_id": explicit_task_id} if explicit_task_id is not None else {}),
                    "command_id": command_id,
                    "log_path": log_path,
                    "deadline_at": (
                        utc_isoformat(deadline_at) if deadline_at is not None else None
                    ),
                    "notification": "once" if args.notify_on_complete else "none",
                    "result_pending": True,
                },
            )
        pieces: list[str] = []
        pending: list[asyncio.Task[None]] = []
        last_emit = 0.0
        trail_task: asyncio.Task[None] | None = None

        def _schedule_update(text: str) -> None:
            if on_update is None:
                return
            payload = AgentToolResult(
                content=[TextContent(text=text)],
                details={"status": "running"},
            )
            try:
                maybe = on_update(payload)
            except Exception:
                logger.exception("execute on_update failed")
                return
            if inspect.isawaitable(maybe):

                async def _await_update(aw: Awaitable[object] = maybe) -> None:
                    await asyncio.shield(aw)

                pending.append(asyncio.create_task(_await_update()))

        def _emit_snapshot() -> None:
            _schedule_update(_bounded_execute_excerpt("".join(pieces)))

        def _cancel_trail() -> None:
            nonlocal trail_task
            if trail_task is not None and not trail_task.done():
                trail_task.cancel()
            trail_task = None

        def _on_chunk(text: str) -> None:
            nonlocal last_emit, trail_task
            if not text:
                return
            pieces.append(text)
            now = time.monotonic()
            remaining = _EXECUTE_UPDATE_INTERVAL_SECONDS - (now - last_emit)
            if remaining > 0:

                async def _trail() -> None:
                    nonlocal last_emit
                    await asyncio.sleep(remaining)
                    last_emit = time.monotonic()
                    _emit_snapshot()

                if trail_task is None or trail_task.done():
                    trail_task = asyncio.create_task(_trail())
                return
            _cancel_trail()
            last_emit = now
            _emit_snapshot()

        async def _drain_updates() -> None:
            _cancel_trail()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
                pending.clear()

        try:
            try:
                if not _is_bare_sleep(args.command) and sandbox.supports_background() is True:
                    command_id = generate_public_id(PREFIX_SANDBOX_COMMAND)
                    log_path = f"{sandbox.workdir.rstrip('/')}/.cubeplex/execute-{command_id}.log"
                    deadline_at = cast(
                        datetime | None,
                        datetime.now(UTC) + timedelta(seconds=background_timeout)
                        if background_timeout is not None
                        else None,
                    )
                    auto_reservation: bool | _ReservedCommand = False
                    auto_task_id: str | None = None
                    auto_persist_error: BaseException | None = None

                    async def _on_started_auto(ref: str) -> None:
                        nonlocal auto_persist_error
                        if persist_running is None:
                            return
                        try:
                            await persist_running(command_id, ref)
                        except Exception as exc:
                            auto_persist_error = exc

                    async with lock:
                        if persist_reserve is not None:
                            try:
                                auto_reservation = await persist_reserve(
                                    command_id=command_id,
                                    tool_call_id=tool_call_id,
                                    command=args.command,
                                    description=args.description,
                                    notify_on_complete=args.notify_on_complete,
                                    log_path=log_path,
                                    timeout_seconds=background_timeout,
                                    monitor_deadline_at=deadline_at,
                                )
                            except Exception as exc:
                                from cubeplex.repositories.sandbox_command import (
                                    SandboxCommandCapError,
                                )

                                if isinstance(exc, SandboxCommandCapError):
                                    raise _AutoBackgroundUnavailable from exc
                                logger.exception("sandbox command reserve failed")
                                return AgentToolResult(
                                    content=[
                                        TextContent(text="failed to reserve background command")
                                    ],
                                    is_error=True,
                                )
                        if isinstance(auto_reservation, _ReservedCommand):
                            command_id = auto_reservation.command_id
                            auto_task_id = auto_reservation.task_id
                            log_path = auto_reservation.log_path or log_path
                            deadline_at = auto_reservation.deadline_at
                            if not auto_reservation.start_allowed:
                                return AgentToolResult(
                                    content=[
                                        TextContent(
                                            text=(
                                                f"Command is already managed in background as "
                                                f"{command_id}."
                                            )
                                        )
                                    ],
                                    details={
                                        "status": "running",
                                        "task_id": auto_task_id,
                                        "command_id": command_id,
                                        "log_path": log_path,
                                        "deadline_at": (
                                            utc_isoformat(deadline_at)
                                            if deadline_at is not None
                                            else None
                                        ),
                                        "notification": (
                                            "once" if args.notify_on_complete else "none"
                                        ),
                                        "result_pending": True,
                                    },
                                )
                        if (
                            not auto_reservation
                            and len(live_commands) >= MAX_LIVE_BACKGROUND_COMMANDS
                        ):
                            raise _AutoBackgroundUnavailable
                        try:
                            handle = await sandbox.start(
                                args.command,
                                timeout=background_timeout,
                                on_started=_on_started_auto,
                            )
                        except Exception:
                            if auto_reservation and persist_start_failed is not None:
                                await persist_start_failed(command_id, "provider start failed")
                            elif auto_reservation and persist_killed is not None:
                                await persist_killed(command_id)
                            raise
                        handle.command_id = command_id
                        handle.deadline_at = deadline_at
                        if auto_persist_error is not None:
                            await sandbox.kill(handle)
                            if persist_start_failed is not None:
                                await persist_start_failed(
                                    command_id,
                                    "provider start receipt persistence failed",
                                )
                            elif persist_killed is not None:
                                await persist_killed(command_id)
                            return AgentToolResult(
                                content=[TextContent(text="failed to persist background command")],
                                is_error=True,
                            )
                        live_commands[command_id] = (handle, args.notify_on_complete)
                    if on_live is not None:
                        on_live()
                    await _write_sandbox_log(sandbox, log_path, b"")
                    bg_deadline = time.monotonic() + AUTO_BACKGROUND_SECONDS
                    kill_at = (
                        time.monotonic() + background_timeout
                        if background_timeout is not None
                        else None
                    )
                    while True:
                        snap = await sandbox.poll(handle)
                        if snap.new_output:
                            _on_chunk(snap.new_output)
                        logs_confirmed = await _ack_snapshot_output(
                            command_id,
                            handle,
                            log_path,
                            snap,
                        )
                        if snap.status != "running":
                            durable_status = (
                                await load_persisted_status(command_id)
                                if load_persisted_status is not None
                                else None
                            )
                            if durable_status == "killed":
                                snap.status = "killed"
                            live_commands.pop(command_id, None)
                            if snap.status == "killed" and persist_killed is not None:
                                await persist_killed(command_id)
                            elif persist_foreground is not None:
                                await persist_foreground(command_id, snap, logs_confirmed)
                            elif persist_discard is not None:
                                await persist_discard(command_id)
                            if snap.status == "killed":
                                output = "".join(pieces)
                                suffix = "\n[killed by user]" if output else "[killed by user]"
                                return AgentToolResult(
                                    content=[TextContent(text=output + suffix)],
                                    details={"status": "killed"},
                                    is_error=True,
                                )
                            if snap.exit_code is not None and snap.exit_code != 0:
                                pieces.append(
                                    f"\n[exit code: {snap.exit_code}]"
                                    if snap.exit_code not in (None, 0)
                                    else ""
                                )
                            output = "".join(pieces)
                            if len(output) > EXECUTE_RESULT_SPILL_CHARS:
                                suffix = f"\n\n[truncated] full output written to {log_path}"
                                output = _bounded_execute_excerpt(output, suffix=suffix)
                            if workspace_id is not None and conversation_id is not None:
                                if snap.exit_code == 0:
                                    _record_executed(workspace_id, conversation_id, args.command)
                            return AgentToolResult(
                                content=[TextContent(text=output)],
                                details={"status": "exited"},
                            )
                        now = time.monotonic()
                        if kill_at is not None and now >= kill_at:
                            await sandbox.kill(handle)
                            confirmed = await sandbox.poll(handle)
                            confirmed_logs = await _ack_snapshot_output(
                                command_id,
                                handle,
                                log_path,
                                confirmed,
                            )
                            if confirmed.status == "running":
                                return AgentToolResult(
                                    content=[
                                        TextContent(
                                            text=(
                                                f"Command exceeded {timeout}s, but termination "
                                                "could not be confirmed."
                                            )
                                        )
                                    ],
                                    details={"status": "running", "command_id": command_id},
                                    is_error=True,
                                )
                            live_commands.pop(command_id, None)
                            if persist_timed_out is not None:
                                await persist_timed_out(
                                    command_id,
                                    args.notify_on_complete,
                                    confirmed,
                                    confirmed_logs,
                                )
                            elif persist_killed is not None:
                                await persist_killed(command_id)
                            return AgentToolResult(
                                content=[TextContent(text=_timeout_tool_message(timeout))],
                                is_error=True,
                            )
                        if now >= bg_deadline:
                            handed_off = handoff is not None and await handoff(command_id)
                            if handoff is not None and not handed_off:
                                bg_deadline = now + _BACKGROUND_POLL_INTERVAL_SECONDS
                                continue
                            if handoff is None and kill_at is not None:
                                _schedule_deadline(
                                    command_id,
                                    handle,
                                    log_path,
                                    kill_at=kill_at,
                                )
                            notice = (
                                f"Task {auto_task_id} is still running as command {command_id}."
                                if auto_task_id is not None
                                else (
                                    f"Command still running; continuing in background as "
                                    f"{command_id}."
                                )
                            )
                            return AgentToolResult(
                                content=[TextContent(text=notice)],
                                details={
                                    "status": "running",
                                    **(
                                        {"task_id": auto_task_id}
                                        if auto_task_id is not None
                                        else {}
                                    ),
                                    "command_id": command_id,
                                    "log_path": log_path,
                                    "deadline_at": (
                                        utc_isoformat(deadline_at)
                                        if deadline_at is not None
                                        else None
                                    ),
                                    "notification": ("once" if args.notify_on_complete else "none"),
                                    "result_pending": True,
                                },
                            )
                        until_background = max(0.0, bg_deadline - now)
                        until_timeout = (
                            max(0.0, kill_at - now) if kill_at is not None else until_background
                        )
                        await asyncio.sleep(
                            min(
                                _BACKGROUND_POLL_INTERVAL_SECONDS,
                                until_background,
                                until_timeout,
                            )
                        )

                result = await sandbox.execute(args.command, timeout=timeout, on_chunk=_on_chunk)
            except _AutoBackgroundUnavailable:
                result = await sandbox.execute(args.command, timeout=timeout, on_chunk=_on_chunk)
            except TimeoutError:
                return AgentToolResult(
                    content=[TextContent(text=_timeout_tool_message(timeout))],
                    is_error=True,
                )
            except Exception as exc:
                if _is_timeout_error(exc):
                    return AgentToolResult(
                        content=[TextContent(text=_timeout_tool_message(timeout))],
                        is_error=True,
                    )
                raise

            if _is_timeout_result(result.output, result.exit_code):
                return AgentToolResult(
                    content=[TextContent(text=_timeout_tool_message(timeout))],
                    is_error=True,
                )
            if workspace_id is not None and conversation_id is not None and result.exit_code == 0:
                _record_executed(workspace_id, conversation_id, args.command)
            output = result.output
            if result.exit_code is not None and result.exit_code != 0:
                output += f"\n[exit code: {result.exit_code}]"
            if len(output) > EXECUTE_RESULT_SPILL_CHARS:
                safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", tool_call_id)[:80] or "tool"
                spill_path = f"{sandbox.workdir.rstrip('/')}/.cubeplex/execute-{safe_id}.log"
                try:
                    await sandbox.upload([(spill_path, output.encode())])
                    suffix = f"\n\n[truncated] full output written to {spill_path}"
                except Exception:
                    logger.exception("execute spill upload failed")
                    suffix = "\n\n[truncated]"
                output = _bounded_execute_excerpt(output, suffix=suffix)
            return AgentToolResult(
                content=[TextContent(text=output)],
                details={"status": "exited"},
            )
        finally:
            await _drain_updates()

    return AgentTool(
        name="execute",
        description=(
            "Execute a shell command in the sandbox environment. "
            "Always set description first (a 5-10 word user-facing summary) "
            "so the chat UI can show it while the command is still streaming. "
            "The default execution deadline is one hour. For installs, downloads, or "
            "builds, pass timeout_seconds when they need a different deadline. "
            "If you hit the limit, "
            "raise timeout_seconds or split the work and retry."
        ),
        parameters=_ExecuteArgs,
        execute=_execute,
    )


class _KillExecuteArgs(BaseModel):
    command_id: str = Field(description="CubePlex command id returned by background execute.")


class _MonitorArgs(BaseModel):
    description: str = Field(description="Short summary of what this monitor watches.")
    command: str
    persistent: bool = Field(
        default=False,
        description=(
            "If true, remove the monitor deadline. This does not enable repeated notices."
        ),
    )
    timeout_seconds: int | None = Field(
        default=3600,
        ge=1,
        le=36000,
        description="Kill after this many seconds unless persistent. Default 3600.",
    )


def _make_monitor_tool(
    sandbox: Sandbox,
    *,
    live: dict[str, tuple[ProcessHandle, bool]],
    live_lock: asyncio.Lock | None = None,
    persist_reserve: Callable[..., Awaitable[bool | _ReservedCommand]] | None = None,
    persist_running: Callable[[str, str], Awaitable[None]] | None = None,
    persist_cursor: Callable[[str, str], Awaitable[None]] | None = None,
    persist_killed: Callable[[str], Awaitable[None]] | None = None,
    persist_start_failed: Callable[[str, str], Awaitable[None]] | None = None,
    persist_monitor_timed_out: Callable[[str], Awaitable[None]] | None = None,
    persist_monitor_observation: (
        Callable[[str, ProcessSnapshot, bool], Awaitable[None]] | None
    ) = None,
    persist_monitor_timeout: (
        Callable[[str, bool, ProcessSnapshot, bool], Awaitable[None]] | None
    ) = None,
    deadline_tasks: dict[str, asyncio.Task[None]] | None = None,
    on_live: Callable[[], None] | None = None,
    handoff: Callable[[str], Awaitable[bool]] | None = None,
) -> AgentTool[_MonitorArgs]:
    lock = live_lock if live_lock is not None else asyncio.Lock()
    timeout_tasks = deadline_tasks if deadline_tasks is not None else {}

    async def _monitor(
        tool_call_id: str,
        args: _MonitorArgs,
        *,
        signal: asyncio.Event | None = None,
        on_update: Callable[[StructuredValue], None] | None = None,
    ) -> AgentToolResult:
        del signal, on_update
        if not sandbox.supports_background():
            return AgentToolResult(
                content=[TextContent(text="This sandbox cannot run background commands.")],
                is_error=True,
            )
        if _looks_like_shell_background(args.command):
            return AgentToolResult(
                content=[
                    TextContent(
                        text=(
                            "Do not background with shell &, nohup, or disown. "
                            "The monitor tool manages the process lifetime."
                        )
                    )
                ],
                is_error=True,
            )
        command_id = generate_public_id(PREFIX_SANDBOX_COMMAND)
        log_path = f"{sandbox.workdir.rstrip('/')}/.cubeplex/execute-{command_id}.log"
        deadline = None
        if not args.persistent:
            seconds = args.timeout_seconds or 3600
            deadline = datetime.now(UTC) + timedelta(seconds=seconds)
        reserved: bool | _ReservedCommand = False
        task_id: str | None = None
        async with lock:
            if persist_reserve is not None:
                try:
                    reserved = await persist_reserve(
                        command_id=command_id,
                        tool_call_id=tool_call_id,
                        command=args.command,
                        description=args.description,
                        notify_on_complete=True,
                        log_path=log_path,
                        kind="monitor",
                        lifetime="conversation",
                        timeout_seconds=None,
                        monitor_deadline_at=deadline,
                    )
                except Exception as exc:
                    from cubeplex.repositories.sandbox_command import SandboxCommandCapError

                    if isinstance(exc, SandboxCommandCapError):
                        return AgentToolResult(
                            content=[TextContent(text=str(exc))],
                            is_error=True,
                        )
                    logger.exception("monitor reserve failed")
                    return AgentToolResult(
                        content=[TextContent(text="failed to reserve monitor")],
                        is_error=True,
                    )
            if isinstance(reserved, _ReservedCommand):
                command_id = reserved.command_id
                task_id = reserved.task_id
                log_path = reserved.log_path or log_path
                deadline = reserved.deadline_at
                if not reserved.start_allowed:
                    return AgentToolResult(
                        content=[TextContent(text=f"Monitor already managed as {command_id}.")],
                        details={
                            "status": "running",
                            "task_id": task_id,
                            "command_id": command_id,
                            "log_path": log_path,
                            "deadline_at": (
                                utc_isoformat(deadline) if deadline is not None else None
                            ),
                            "notification": "once",
                            "result_pending": True,
                        },
                    )
            if not reserved and len(live) >= MAX_LIVE_BACKGROUND_COMMANDS:
                return AgentToolResult(
                    content=[TextContent(text="at most 8 running commands per sandbox")],
                    is_error=True,
                )
            persist_error: BaseException | None = None

            async def _on_started(ref: str) -> None:
                nonlocal persist_error
                if persist_running is None:
                    return
                try:
                    await persist_running(command_id, ref)
                except Exception as exc:
                    persist_error = exc

            try:
                handle = await sandbox.start(args.command, on_started=_on_started)
            except Exception:
                if reserved and persist_start_failed is not None:
                    await persist_start_failed(command_id, "provider start failed")
                elif reserved and persist_killed is not None:
                    await persist_killed(command_id)
                raise
            handle.command_id = command_id
            handle.deadline_at = deadline
            if persist_error is not None:
                await sandbox.kill(handle)
                if persist_start_failed is not None:
                    await persist_start_failed(
                        command_id,
                        "provider start receipt persistence failed",
                    )
                elif persist_killed is not None:
                    await persist_killed(command_id)
                return AgentToolResult(
                    content=[TextContent(text="failed to persist monitor")],
                    is_error=True,
                )
            live[command_id] = (handle, False)
        if deadline is not None:

            async def _enforce_monitor_deadline() -> None:
                await asyncio.sleep(max(0.0, (deadline - datetime.now(UTC)).total_seconds()))
                try:
                    async with lock:
                        current = live.get(command_id)
                        if current is None or current[0] is not handle:
                            return
                        deadline_snapshot = await sandbox.poll(handle)
                        logs_confirmed = True
                        if deadline_snapshot.new_output:
                            appended = await _append_sandbox_log(
                                sandbox,
                                log_path,
                                deadline_snapshot.new_output,
                            )
                            logs_confirmed = appended.data_written
                        if logs_confirmed and deadline_snapshot.log_cursor is not None:
                            if persist_cursor is not None:
                                await persist_cursor(command_id, deadline_snapshot.log_cursor)
                            handle.log_cursor = deadline_snapshot.log_cursor
                        if deadline_snapshot.status != "running":
                            live.pop(command_id, None)
                            if persist_monitor_observation is not None:
                                await persist_monitor_observation(
                                    command_id,
                                    deadline_snapshot,
                                    logs_confirmed,
                                )
                            return
                        if deadline_snapshot.status == "running":
                            await sandbox.kill(handle)
                            confirmed = await sandbox.poll(handle)
                            confirmed_logs = True
                            if confirmed.new_output:
                                appended = await _append_sandbox_log(
                                    sandbox,
                                    log_path,
                                    confirmed.new_output,
                                )
                                confirmed_logs = appended.data_written
                            if confirmed_logs and confirmed.log_cursor is not None:
                                if persist_cursor is not None:
                                    await persist_cursor(command_id, confirmed.log_cursor)
                                handle.log_cursor = confirmed.log_cursor
                            live.pop(command_id, None)
                            if persist_monitor_timeout is not None:
                                await persist_monitor_timeout(
                                    command_id,
                                    True,
                                    confirmed,
                                    confirmed_logs,
                                )
                            elif persist_monitor_timed_out is not None:
                                await persist_monitor_timed_out(command_id)
                except Exception:
                    logger.exception("failed to enforce monitor deadline {}", command_id)

            task = asyncio.create_task(_enforce_monitor_deadline())
            timeout_tasks[command_id] = task
            task.add_done_callback(lambda _task: timeout_tasks.pop(command_id, None))
        await _write_sandbox_log(sandbox, log_path, b"")
        handed_off = handoff is not None and await handoff(command_id)
        if not handed_off and on_live is not None:
            on_live()
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        f"Monitor task {task_id} is running as command {command_id}. "
                        "It will send one final notice."
                        if task_id is not None
                        else f"Monitor running as {command_id}. Use kill_execute to stop."
                    )
                )
            ],
            details={
                "status": "running",
                **({"task_id": task_id} if task_id is not None else {}),
                "command_id": command_id,
                "log_path": log_path,
                "deadline_at": utc_isoformat(deadline) if deadline is not None else None,
                "notification": "once",
                "result_pending": True,
            },
        )

    return AgentTool(
        name="monitor",
        description=(
            "Run one condition-waiting script in the background. The script must keep "
            "checking by itself, exit 0 when the condition is satisfied, and exit nonzero "
            "on failure. CubePlex sends exactly one final notice for success, failure, or "
            "timeout; stdout is only a log and does not wake the agent repeatedly."
        ),
        parameters=_MonitorArgs,
        execute=_monitor,
    )


def _make_kill_execute_tool(
    sandbox: Sandbox,
    live: dict[str, tuple[ProcessHandle, bool]],
    *,
    persist_killed: Callable[[str], Awaitable[None]] | None = None,
    kill_persisted: Callable[[str], Awaitable[bool]] | None = None,
    live_lock: asyncio.Lock | None = None,
    deadline_tasks: dict[str, asyncio.Task[None]] | None = None,
) -> AgentTool[_KillExecuteArgs]:
    lock = live_lock if live_lock is not None else asyncio.Lock()

    async def _kill(
        tool_call_id: str,
        args: _KillExecuteArgs,
        *,
        signal: asyncio.Event | None = None,
        on_update: Callable[[StructuredValue], None] | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        async with lock:
            entry = live.get(args.command_id)
        if entry is None:
            if kill_persisted is not None:
                try:
                    if await kill_persisted(args.command_id):
                        return AgentToolResult(
                            content=[TextContent(text=f"stop requested for {args.command_id}")],
                            details={"status": "stopping", "command_id": args.command_id},
                        )
                except Exception:
                    logger.exception("durable kill_execute failed for {}", args.command_id)
                    return AgentToolResult(
                        content=[TextContent(text=f"failed to kill {args.command_id}")],
                        is_error=True,
                    )
            return AgentToolResult(
                content=[TextContent(text=f"command not found: {args.command_id}")],
                is_error=True,
            )
        handle, _notify = entry
        try:
            await sandbox.kill(handle)
            confirmed = await sandbox.poll(handle)
        except Exception:
            logger.exception("kill_execute failed for {}", args.command_id)
            return AgentToolResult(
                content=[TextContent(text=f"failed to kill {args.command_id}")],
                is_error=True,
            )
        if confirmed.status == "running":
            return AgentToolResult(
                content=[TextContent(text=f"failed to confirm kill for {args.command_id}")],
                is_error=True,
            )
        async with lock:
            live.pop(args.command_id, None)
        deadline_task = deadline_tasks.pop(args.command_id, None) if deadline_tasks else None
        if deadline_task is not None:
            deadline_task.cancel()
        if persist_killed is not None:
            try:
                await persist_killed(args.command_id)
            except Exception:
                logger.exception("sandbox command kill persist failed")
        return AgentToolResult(
            content=[TextContent(text=f"killed {args.command_id}")],
            details={"status": "killed", "command_id": args.command_id},
        )

    return AgentTool(
        name="kill_execute",
        description="Stop a background sandbox command started with execute(background=true).",
        parameters=_KillExecuteArgs,
        execute=_kill,
    )


def _make_write_file_tool(sandbox: Sandbox) -> AgentTool[_WriteFileArgs]:
    """Build the write_file cubeloop.AgentTool backed by a sandbox instance."""

    async def _write_file(
        tool_call_id: str,
        args: _WriteFileArgs,
        *,
        signal: asyncio.Event | None = None,
        on_update: Callable[[StructuredValue], None] | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        # Overwrite guard: by default refuse to clobber an existing file. This
        # stops an agent from silently overwriting a pre-existing file (e.g. a
        # safety test where a summary.md already exists) — it gets a clear error
        # naming the file and can either pick a new name or pass overwrite=true
        # when replacing is the explicit intent. Use `test -f` (not download) so
        # a missing file doesn't trigger a LazySandbox recreate.
        if not args.overwrite:
            check = await sandbox.execute(
                f"test -f {_shquote(args.file_path)} && echo EXISTS || echo MISSING"
            )
            if "EXISTS" in check.output:
                return AgentToolResult(
                    content=[
                        TextContent(
                            text=(
                                f"Error: {args.file_path} already exists. write refuses to "
                                f"overwrite an existing file by default (this protects against "
                                f"silently clobbering work). Either choose a different path, or "
                                f"call write again with overwrite=true if replacing this "
                                f"file is the explicit intent."
                            )
                        )
                    ],
                    is_error=True,
                )

        await sandbox.upload([(args.file_path, args.content.encode())])
        return AgentToolResult(content=[TextContent(text=f"Successfully wrote {args.file_path}")])

    return AgentTool(
        name="write",
        description=(
            "Create a file with the given content. By default refuses to overwrite an "
            "existing file at the path (returns an error so you can rename or confirm); "
            "pass overwrite=true to replace an existing file when that is the explicit intent."
        ),
        parameters=_WriteFileArgs,
        execute=_write_file,
    )


_SMART_SINGLE_QUOTES = re.compile(r"[‘’‚‛]")
_SMART_DOUBLE_QUOTES = re.compile(r"[“”„‟]")
_UNICODE_DASHES = re.compile(r"[‐‑‒–—―−]")
_UNICODE_SPACES = re.compile(r"[  -   　]")


def _normalize_for_fuzzy(text: str) -> str:
    """Normalize text for fuzzy matching.

    Applies the same transforms as pi's normalizeForFuzzyMatch: NFKC,
    trailing whitespace per line, smart quotes/dashes/spaces → ASCII.
    Only used for match location — replacements are always applied to the
    original bytes so non-matched content is never altered.
    """
    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _SMART_SINGLE_QUOTES.sub("'", text)
    text = _SMART_DOUBLE_QUOTES.sub('"', text)
    text = _UNICODE_DASHES.sub("-", text)
    text = _UNICODE_SPACES.sub(" ", text)
    return text


def _partial_normalize(text: str) -> str:
    """Apply all fuzzy normalizations except trailing-whitespace stripping.

    This is character-stable: each original char maps to \u22651 output chars with
    a predictable position mapping. Used by _fuzzy_replace to build a
    position map before applying the trailing-whitespace stripping step.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _SMART_SINGLE_QUOTES.sub("'", text)
    text = _SMART_DOUBLE_QUOTES.sub('"', text)
    text = _UNICODE_DASHES.sub("-", text)
    text = _UNICODE_SPACES.sub(" ", text)
    return text


def _fuzzy_match_span(current: str, old_string: str) -> tuple[int, int] | None:
    """Find the original span for one uniquely fuzzy-matched old_string.

    Returns ``(start, end)`` in the un-normalized content, or None if the
    normalized old string cannot be mapped back to the original content.

    Two-phase strategy to avoid the trailing-whitespace-stripping complication:
    1. Apply NFKC + substitutions (character-stable) to get `partial`.
       Build partial_to_orig: for each position in `partial`, which original index?
    2. Strip trailing whitespace from `partial` line-by-line (same as _normalize_for_fuzzy).
       Track which positions in `partial` survive into norm_content.
    3. Use those maps to convert norm_start/norm_end back to original positions.
    """
    norm_content = _normalize_for_fuzzy(current)
    norm_old = _normalize_for_fuzzy(old_string)

    if norm_content.count(norm_old) != 1:
        return None

    norm_start = norm_content.index(norm_old)
    norm_end = norm_start + len(norm_old)

    # Phase 1: character-stable normalization -> partial
    partial = _partial_normalize(current)

    # Build partial_to_orig: partial[i] came from current[partial_to_orig[i]]
    partial_to_orig: list[int] = []
    for orig_idx, ch in enumerate(current):
        n = _partial_normalize(ch)
        partial_to_orig.extend([orig_idx] * len(n))

    # Phase 2: strip trailing whitespace from partial line-by-line -> norm_content.
    # Record which positions in partial survive into norm_content.
    partial_lines = partial.split("\n")
    partial_survived: list[int] = []  # partial positions that appear in norm_content
    p_pos = 0
    for li, line in enumerate(partial_lines):
        stripped = line.rstrip()
        for j in range(len(stripped)):
            partial_survived.append(p_pos + j)
        p_pos += len(line)
        if li < len(partial_lines) - 1:
            partial_survived.append(p_pos)  # the \n that joins lines
            p_pos += 1

    if len(partial_survived) != len(norm_content):
        return None

    orig_start = partial_to_orig[partial_survived[norm_start]]
    orig_end = partial_to_orig[partial_survived[norm_end - 1]] + 1

    return orig_start, orig_end


def _apply_edit_spans(current: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply edits whose offsets all refer to the same original content."""
    updated = current
    for start, end, new_string in sorted(edits, reverse=True):
        updated = updated[:start] + new_string + updated[end:]
    return updated


def _first_changed_line(current: str, updated: str) -> int | None:
    """Return the 1-indexed first line changed between two text values."""
    before_lines = current.splitlines(keepends=True)
    after_lines = updated.splitlines(keepends=True)
    for index, (before, after) in enumerate(zip(before_lines, after_lines, strict=False), start=1):
        if before != after:
            return index
    if len(before_lines) != len(after_lines):
        return min(len(before_lines), len(after_lines)) + 1
    return None


def _make_edit_file_tool(sandbox: Sandbox) -> AgentTool[_EditFileArgs]:
    """Build the edit_file cubeloop.AgentTool backed by a sandbox instance."""

    async def _edit_file(
        tool_call_id: str,
        args: _EditFileArgs,
        *,
        signal: asyncio.Event | None = None,
        on_update: Callable[[StructuredValue], None] | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        edit_specs = args.edits or [
            _EditSpec(old_string=args.old_string or "", new_string=args.new_string or "")
        ]
        for index, edit in enumerate(edit_specs, start=1):
            if edit.old_string == edit.new_string:
                return AgentToolResult(
                    content=[
                        TextContent(
                            text=f"Error: edit {index}: old_string and new_string must differ."
                        )
                    ]
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

        matched_edits: list[tuple[int, int, str, int]] = []
        fuzzy_matched = False
        for index, edit in enumerate(edit_specs, start=1):
            count = current.count(edit.old_string)
            if count == 1:
                start = current.index(edit.old_string)
                end = start + len(edit.old_string)
            elif count > 1:
                return AgentToolResult(
                    content=[
                        TextContent(
                            text=(
                                f"Error: edit {index}: old_string appears {count} times in "
                                f"{args.file_path}. It must be unique — provide more context."
                            )
                        )
                    ]
                )
            else:
                norm_count = _normalize_for_fuzzy(current).count(
                    _normalize_for_fuzzy(edit.old_string)
                )
                if norm_count == 0:
                    return AgentToolResult(
                        content=[
                            TextContent(
                                text=f"Error: edit {index}: old_string not found in "
                                f"{args.file_path}"
                            )
                        ]
                    )
                if norm_count > 1:
                    return AgentToolResult(
                        content=[
                            TextContent(
                                text=(
                                    f"Error: edit {index}: old_string appears "
                                    f"{norm_count} times in {args.file_path}. "
                                    "It must be unique — provide more context."
                                )
                            )
                        ]
                    )
                span = _fuzzy_match_span(current, edit.old_string)
                if span is None:
                    return AgentToolResult(
                        content=[
                            TextContent(
                                text=f"Error: edit {index}: old_string not found in "
                                f"{args.file_path}"
                            )
                        ]
                    )
                start, end = span
                fuzzy_matched = True
            matched_edits.append((start, end, edit.new_string, index))

        ordered = sorted(matched_edits)
        for (_, previous_end, _, previous_index), (start, _, _, edit_index) in zip(
            ordered, ordered[1:], strict=False
        ):
            if start < previous_end:
                return AgentToolResult(
                    content=[
                        TextContent(
                            text=(
                                f"Error: edit {previous_index} overlaps edit {edit_index} "
                                f"in {args.file_path}; provide separate, non-overlapping "
                                "old_string values."
                            )
                        )
                    ]
                )

        updated = _apply_edit_spans(
            current, [(start, end, new) for start, end, new, _ in matched_edits]
        )

        await sandbox.upload([(args.file_path, updated.encode())])

        def _lines_for_diff(text: str) -> list[str]:
            lines = text.splitlines(keepends=True)
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            return lines

        diff_lines = list(
            difflib.unified_diff(
                _lines_for_diff(current),
                _lines_for_diff(updated),
                fromfile=f"a/{args.file_path}",
                tofile=f"b/{args.file_path}",
                n=4,
            )
        )
        suffix = " (fuzzy match)" if fuzzy_matched else ""
        return AgentToolResult(
            content=[
                TextContent(
                    text=f"Successfully edited {args.file_path}{suffix} "
                    f"({len(edit_specs)} edit{'s' if len(edit_specs) != 1 else ''})"
                )
            ],
            details={
                "file_path": args.file_path,
                "unified_diff": "".join(diff_lines),
                "fuzzy_matched": fuzzy_matched,
                "edit_count": len(edit_specs),
                "match_mode": "fuzzy" if fuzzy_matched else "exact",
                "first_changed_line": _first_changed_line(current, updated),
            },
        )

    return AgentTool(
        name="edit",
        description=(
            "Apply one or more unique, non-overlapping text edits to an existing file in one "
            "call. When making multiple changes to the same file, include them all in edits."
        ),
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
- Remote URLs — this tool only reads sandbox paths. Use a web-fetch
  tool for URLs.
- Grep / search — for pattern-find, execute("rg -n 'pattern' <file>")
  is more direct than read + scan.
- Tiny known-offset peeks — execute("sed -n '42p' <file>") skips
  parser overhead.

HOW UNSUPPORTED FORMATS BEHAVE:
- The tool returns kind="unsupported" with a `hint` when no parser
  plugin handles the file's MIME type. Common cases — video, audio,
  archives, binary executables — fall here in the default deployment.
- The `hint` field tells you what alternative to try (e.g., for
  archives: extract first via execute("unzip <file>") then read
  on extracted files).
- If you see kind="unsupported", surface the hint to the user; don't
  retry read on the same path.

RETURN FORMAT (discriminated by `kind`):
- "text"        : {content, mime, size_bytes, truncated, metadata}
- "notebook"    : {cells: [{cell_type, source, outputs}, ...]}
- "unsupported" : {reason, hint, mime, size_bytes}
- "unchanged"   : file unchanged since previous read in this session
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
  read(path, line_range=f"{N}-") to continue from there.
- PDF/DOCX/PPTX (best-effort): read metadata.next_page_to_read
  and call read(path, page_range=f"{N}-"). If the field
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
    """Build the file_read cubeloop.AgentTool backed by a sandbox + (optional) conversation."""

    async def _file_read(
        tool_call_id: str,
        args: _FileReadArgs,
        *,
        signal: asyncio.Event | None = None,
        on_update: Callable[[StructuredValue], None] | None = None,
    ) -> AgentToolResult:
        import json

        from cubeplex.parsers.schema import ErrorOutput
        from cubeplex.sandbox.base import SandboxError

        del tool_call_id, signal, on_update

        # Surface FileNotFoundError / SandboxError as a structured ErrorOutput
        # instead of letting them bubble into cubeloop's generic tool-error
        # wrapper — that wrapper writes ``str(exc)`` into the tool result, and
        # ``str(FileNotFoundError(path))`` is literally the path, which the
        # model reads as "the file content is its own path".
        try:
            result: Any = await sandbox.file_read(
                args.path,
                options=ParseOptions(page_range=args.page_range, line_range=args.line_range),
                conversation_id=conversation_id,
            )
        except FileNotFoundError:
            result = ErrorOutput(
                path=args.path,
                error=f"file not found: {args.path}",
                retryable=False,
            )
        except SandboxError as exc:
            result = ErrorOutput(
                path=args.path,
                error=f"sandbox error reading {args.path}: {exc}",
                retryable=False,
            )
        except Exception as exc:  # noqa: BLE001 — last-resort wrapper
            # Catch-all for transport errors (httpx.TransportError, timeouts)
            # and anything else the download / sniff / dedup layers can raise
            # outside the parser-registry's own try/except. Without this,
            # cubeloop's generic tool-error wrapper writes str(exc) into the
            # ToolResult content — the same trap that FileNotFoundError fell
            # into. asyncio.CancelledError is BaseException, not Exception,
            # so user-cancel still propagates.
            result = ErrorOutput(
                path=args.path,
                error=f"failed to read {args.path}: {exc}",
                retryable=True,
            )
        return AgentToolResult(content=[TextContent(text=json.dumps(result.model_dump()))])

    return AgentTool(
        name="read",
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
        config_loader: SandboxConfigLoader | Callable[[], Awaitable[dict[str, Any]]] | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        run_id: str | None = None,
        admission_id: str | None = None,
        owner_token: str | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.conversation_id = conversation_id
        self.workspace_id = workspace_id
        self.command_rules = command_rules or []
        self.channel = channel
        self.config_loader = config_loader
        self.org_id = org_id
        self.user_id = user_id
        self.run_id = run_id
        self.admission_id = admission_id
        self._task_owner_token = owner_token
        self._session_factory = session_factory
        self._live_commands: dict[str, tuple[ProcessHandle, bool]] = {}
        self._command_deadline_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_bindings: dict[str, _TaskCommandBinding] = {}
        self._live_lock = asyncio.Lock()
        self._owner_id = f"run:{run_id}" if run_id else "run:local"
        self._lease_task: asyncio.Task[None] | None = None

        self._tools: list[AgentTool[Any]] = [
            _make_execute_tool(
                sandbox,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                live=self._live_commands,
                live_lock=self._live_lock,
                persist_reserve=self._persist_reserve,
                persist_running=self._persist_running,
                persist_cursor=self._persist_cursor,
                persist_killed=self._persist_killed,
                persist_start_failed=self._persist_start_failed,
                persist_exited=self._persist_exited,
                persist_discard=self._persist_discard,
                persist_foreground=self._persist_foreground,
                persist_background_terminal=self._persist_background_terminal,
                persist_timed_out=self._persist_timed_out,
                load_persisted_status=self._persisted_command_status,
                deadline_tasks=self._command_deadline_tasks,
                on_live=self._ensure_lease_task,
                handoff=(
                    self._handoff_conversation_command if session_factory is not None else None
                ),
            ),
            _make_kill_execute_tool(
                sandbox,
                self._live_commands,
                persist_killed=self._persist_killed,
                kill_persisted=self._kill_persisted_command,
                live_lock=self._live_lock,
                deadline_tasks=self._command_deadline_tasks,
            ),
            _make_monitor_tool(
                sandbox,
                live=self._live_commands,
                live_lock=self._live_lock,
                persist_reserve=self._persist_reserve,
                persist_running=self._persist_running,
                persist_cursor=self._persist_cursor,
                persist_killed=self._persist_killed,
                persist_start_failed=self._persist_start_failed,
                persist_monitor_timed_out=self._persist_monitor_timed_out,
                persist_monitor_observation=self._persist_monitor_observation,
                persist_monitor_timeout=self._persist_monitor_timeout,
                deadline_tasks=self._command_deadline_tasks,
                on_live=self._ensure_lease_task,
                handoff=self._handoff_conversation_command,
            ),
            _make_write_file_tool(sandbox),
            _make_edit_file_tool(sandbox),
            _make_file_read_tool(sandbox, conversation_id),
        ]
        if config_loader is not None:
            self._tools.append(create_sandbox_config_tool(config_loader))

    @property
    def tools(self) -> list[AgentTool[Any]]:
        """Return the cubeloop.AgentTool list for this middleware."""
        return list(self._tools)

    def _task_scope(self) -> tuple[str, str]:
        if self.org_id is None or self.workspace_id is None:
            raise RuntimeError("background task scope is unavailable")
        return self.org_id, self.workspace_id

    async def on_run_end(
        self,
        ctx: AgentContext,
        *,
        signal: asyncio.Event | None = None,
    ) -> list[UserMessage | AssistantMessage | ToolResultMessage] | None:
        """Hand off work once; task delivery starts a later run when needed."""
        del ctx, signal
        await self._release_managed_commands()
        return None

    async def _release_managed_commands(self) -> None:
        """Transfer every durable command without extending the agent run."""
        for command_id in list(self._live_commands):
            try:
                await self._handoff_conversation_command(command_id)
            except Exception:
                logger.exception("failed to hand off sandbox command {}", command_id)

    async def _handoff_conversation_command(self, command_id: str) -> bool:
        """Release one durable command so the coordinator can poll it immediately."""
        binding = self._task_bindings.get(command_id)
        if binding is not None and self._session_factory is not None:
            from cubeplex.services.background_tasks import BackgroundTaskService

            now = datetime.now(UTC)
            org_id, workspace_id = self._task_scope()
            async with self._session_factory() as session:
                service = BackgroundTaskService(
                    session,
                    org_id=org_id,
                    workspace_id=workspace_id,
                )
                handoff_error: ValueError | None = None
                try:
                    await service.handoff_task(
                        task_id=binding.task_id,
                        owner_token=binding.owner_token,
                        now=now,
                    )
                except ValueError as exc:
                    handoff_error = exc
                await service.defer_owner(
                    task_id=binding.task_id,
                    owner_token=binding.owner_token,
                    now=now,
                    retry_at=now + timedelta(microseconds=1),
                )
                await session.commit()
            async with self._live_lock:
                self._live_commands.pop(command_id, None)
                deadline_task = self._command_deadline_tasks.pop(command_id, None)
                if deadline_task is not None:
                    deadline_task.cancel()
            if handoff_error is not None:
                raise handoff_error
            return True
        async with self._command_repo_ctx() as repo:
            if repo is None:
                return False
            await repo.release_owner([command_id], owner_id=self._owner_id)
        async with self._live_lock:
            self._live_commands.pop(command_id, None)
            deadline_task = self._command_deadline_tasks.pop(command_id, None)
            if deadline_task is not None:
                deadline_task.cancel()
        return True

    async def finalize_run(self) -> None:
        """Release durable work and stop only unreserved local work."""
        lease_task = self._lease_task
        self._lease_task = None
        if lease_task is not None and lease_task is not asyncio.current_task():
            lease_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease_task

        await self._release_managed_commands()

        async with self._live_lock:
            remaining = dict(self._live_commands)
            self._live_commands.clear()
            deadline_tasks = list(self._command_deadline_tasks.values())
            self._command_deadline_tasks.clear()
            for task in deadline_tasks:
                task.cancel()
        for command_id, (handle, _notify) in remaining.items():
            if command_id in self._task_bindings:
                continue
            try:
                await self.sandbox.kill(handle)
            except Exception:
                logger.exception("failed to stop sandbox command {} during run cleanup", command_id)
                continue
            await self._persist_killed(command_id)

    async def _persist_reserve(
        self,
        *,
        command_id: str,
        tool_call_id: str,
        command: str,
        description: str,
        notify_on_complete: bool,
        log_path: str,
        kind: str = "execute",
        lifetime: str | None = None,
        timeout_seconds: int | None = None,
        monitor_deadline_at: datetime | None = None,
    ) -> bool | _ReservedCommand:
        if self._session_factory is None:
            return False
        ensure = getattr(self.sandbox, "ensure_created", None)
        if callable(ensure):
            maybe = ensure()
            if inspect.isawaitable(maybe):
                await maybe
        user_sandbox_id = self.sandbox.user_sandbox_id
        if (
            not isinstance(user_sandbox_id, str)
            or self.conversation_id is None
            or self.org_id is None
            or self.workspace_id is None
            or self.user_id is None
        ):
            return False
        if self.admission_id is not None and self._task_owner_token is not None:
            from cubeplex.models import UserSandbox
            from cubeplex.models.sandbox_command import SandboxCommandKind
            from cubeplex.services.background_tasks import (
                BackgroundTaskService,
                CommandExecutionDetails,
                TaskSpec,
            )

            now = datetime.now(UTC)
            async with self._session_factory() as session:
                sandbox_row = await session.get(UserSandbox, user_sandbox_id)
                if sandbox_row is None or sandbox_row.sandbox_id is None:
                    raise LookupError("sandbox attachment is unavailable")
                service = BackgroundTaskService(
                    session,
                    org_id=self.org_id,
                    workspace_id=self.workspace_id,
                )
                reservation = await service.reserve_task(
                    admission_id=self.admission_id,
                    task_spec=TaskSpec(
                        originating_run_id=self.run_id or "",
                        tool_call_id=tool_call_id,
                        description=description,
                        notify_on_complete=notify_on_complete,
                    ),
                    execution_details=CommandExecutionDetails(
                        user_sandbox_id=user_sandbox_id,
                        sandbox_instance_id=sandbox_row.sandbox_id,
                        provider=sandbox_row.provider,
                        command=command,
                        log_path=log_path,
                        kind=SandboxCommandKind(kind),
                        timeout_seconds=timeout_seconds,
                        monitor_deadline_at=monitor_deadline_at,
                    ),
                    owner_token=self._task_owner_token,
                    owner_until=now + timedelta(seconds=_TASK_OWNER_LEASE_SECONDS),
                    now=now,
                )
                start_allowed = reservation.created and await service.begin_start(
                    task_id=reservation.task.id,
                    owner_token=self._task_owner_token,
                    now=now,
                )
                await session.commit()
            self._task_bindings[reservation.command.id] = _TaskCommandBinding(
                task_id=reservation.task.id,
                owner_token=self._task_owner_token,
                sandbox_instance_id=reservation.command.sandbox_instance_id or "",
            )
            return _ReservedCommand(
                command_id=reservation.command.id,
                task_id=reservation.task.id,
                start_allowed=start_allowed,
                log_path=reservation.command.log_path,
                deadline_at=reservation.task.deadline_at,
            )
        from cubeplex.sandbox.command_coordinator import COMMAND_LEASE_SECONDS

        async with self._command_repo_ctx() as repo:
            if repo is None:
                return False
            await repo.reserve(
                user_sandbox_id=user_sandbox_id,
                conversation_id=self.conversation_id,
                run_id=self.run_id or "",
                tool_call_id=tool_call_id,
                started_by_user_id=self.user_id,
                command=command,
                description=description,
                notify_on_complete=notify_on_complete,
                owner_id=self._owner_id,
                owner_until=datetime.now(UTC) + timedelta(seconds=COMMAND_LEASE_SECONDS),
                log_path=log_path,
                command_id=command_id,
                kind=kind,
                lifetime=lifetime
                if lifetime is not None
                else ("conversation" if not notify_on_complete else "run"),
                monitor_deadline_at=monitor_deadline_at,
            )
        return True

    async def _persist_running(self, command_id: str, provider_ref: str) -> None:
        binding = self._task_bindings.get(command_id)
        if binding is not None and self._session_factory is not None:
            from cubeplex.services.background_tasks import BackgroundTaskService

            org_id, workspace_id = self._task_scope()
            async with self._session_factory() as session:
                await BackgroundTaskService(
                    session,
                    org_id=org_id,
                    workspace_id=workspace_id,
                ).register_start_receipt(
                    task_id=binding.task_id,
                    start_token=binding.owner_token,
                    sandbox_instance_id=binding.sandbox_instance_id,
                    provider_ref=provider_ref,
                    now=datetime.now(UTC),
                )
                await session.commit()
            return
        async with self._command_repo_ctx() as repo:
            if repo is None:
                return
            ok = await repo.mark_running(
                command_id, provider_ref=provider_ref, owner_id=self._owner_id
            )
            if not ok:
                raise RuntimeError(f"mark_running cas missed for {command_id}")

    async def _persist_cursor(self, command_id: str, log_cursor: str) -> None:
        binding = self._task_bindings.get(command_id)
        if binding is not None and self._session_factory is not None:
            from cubeplex.models import SandboxCommand
            from cubeplex.sandbox.base import ProcessSnapshot
            from cubeplex.services.background_tasks import BackgroundTaskService

            org_id, workspace_id = self._task_scope()
            async with self._session_factory() as session:
                command = await session.get(SandboxCommand, command_id)
                if command is None:
                    raise LookupError("task command not found")
                await BackgroundTaskService(
                    session,
                    org_id=org_id,
                    workspace_id=workspace_id,
                ).record_observation(
                    task_id=binding.task_id,
                    owner_token=binding.owner_token,
                    snapshot=ProcessSnapshot(status="running"),
                    log_state="pending",
                    expected_log_cursor=command.log_cursor,
                    confirmed_log_cursor=log_cursor,
                    now=datetime.now(UTC),
                )
                await session.commit()
            return
        async with self._command_repo_ctx() as repo:
            if repo is None:
                return
            ok = await repo.update_log_cursor(
                command_id,
                log_cursor=log_cursor,
                owner_id=self._owner_id,
            )
            if not ok:
                raise RuntimeError(f"update_log_cursor cas missed for {command_id}")

    async def _persist_killed(self, command_id: str) -> None:
        await self._persist_terminal(
            command_id,
            status="killed",
            exit_code=None,
            notify=False,
        )

    async def _persist_start_failed(self, command_id: str, message: str) -> None:
        binding = self._task_bindings.get(command_id)
        if binding is None or self._session_factory is None:
            await self._persist_killed(command_id)
            return
        from cubeplex.services.background_tasks import BackgroundTaskService

        now = datetime.now(UTC)
        org_id, workspace_id = self._task_scope()
        async with self._session_factory() as session:
            service = BackgroundTaskService(
                session,
                org_id=org_id,
                workspace_id=workspace_id,
            )
            await service.record_observation_failure(
                task_id=binding.task_id,
                owner_token=binding.owner_token,
                now=now,
                message=message,
            )
            await service.defer_owner(
                task_id=binding.task_id,
                owner_token=binding.owner_token,
                now=now,
                retry_at=now + timedelta(seconds=1),
            )
            await session.commit()

    async def _persist_exited(
        self,
        command_id: str,
        exit_code: int | None,
        notify: bool,
    ) -> None:
        await self._persist_terminal(
            command_id,
            status="exited",
            exit_code=exit_code,
            notify=notify,
        )

    async def _persist_timed_out(
        self,
        command_id: str,
        notify: bool = True,
        snapshot: ProcessSnapshot | None = None,
        logs_confirmed: bool = True,
    ) -> None:
        terminal = snapshot or ProcessSnapshot(status="killed")
        if await self._persist_task_observation(
            command_id,
            terminal,
            log_state="complete" if logs_confirmed else "retrying",
            stop_reason=TaskStopReason.deadline,
            logs_confirmed=logs_confirmed,
        ):
            return
        if not logs_confirmed:
            return
        await self._persist_terminal(
            command_id,
            status="killed",
            exit_code=None,
            notify=notify,
        )

    async def _persist_monitor_timed_out(self, command_id: str) -> None:
        async with self._command_repo_ctx() as repo:
            if repo is None:
                return
            row = await repo.get(command_id)
            if row is None:
                return
            from cubeplex.sandbox.command_coordinator import _terminalize

            await _terminalize(
                repo.session,
                row,
                status="killed",
                exit_code=None,
                now=datetime.now(UTC),
                sandbox=self.sandbox,
                interrupt=False,
                wake_text="monitor timeout reached",
            )

    async def _persist_monitor_observation(
        self,
        command_id: str,
        snapshot: ProcessSnapshot,
        logs_confirmed: bool,
    ) -> None:
        if await self._persist_task_observation(
            command_id,
            snapshot,
            log_state="complete" if logs_confirmed else "retrying",
            logs_confirmed=logs_confirmed,
        ):
            return
        if not logs_confirmed:
            return
        async with self._command_repo_ctx() as repo:
            if repo is None:
                return
            row = await repo.get(command_id)
            if row is None:
                return
            from cubeplex.sandbox.command_coordinator import _terminalize

            lines = [line for line in snapshot.new_output.splitlines() if line.strip()]

            await _terminalize(
                repo.session,
                row,
                status=snapshot.status,
                exit_code=snapshot.exit_code,
                now=datetime.now(UTC),
                sandbox=None,
                interrupt=False,
                wake_text=lines[-1][-4000:] if lines else f"monitor {snapshot.status}",
            )

    async def _persist_monitor_timeout(
        self,
        command_id: str,
        notify: bool,
        snapshot: ProcessSnapshot,
        logs_confirmed: bool,
    ) -> None:
        del notify
        if await self._persist_task_observation(
            command_id,
            snapshot,
            log_state="complete" if logs_confirmed else "retrying",
            stop_reason=TaskStopReason.deadline,
            logs_confirmed=logs_confirmed,
        ):
            return
        if not logs_confirmed:
            return
        await self._persist_monitor_timed_out(command_id)

    async def _persisted_command_status(self, command_id: str) -> str | None:
        if command_id in self._task_bindings and self._session_factory is not None:
            from cubeplex.models import SandboxCommand

            async with self._session_factory() as session:
                command = await session.get(SandboxCommand, command_id)
                return command.status if command is not None else None
        async with self._command_repo_ctx() as repo:
            if repo is None:
                return None
            row = await repo.get(command_id)
            return row.status if row is not None else None

    async def _persist_discard(self, command_id: str) -> None:
        async with self._command_repo_ctx() as repo:
            if repo is None:
                return
            await repo.discard_reservation(command_id, owner_id=self._owner_id)

    async def _persist_task_observation(
        self,
        command_id: str,
        snapshot: ProcessSnapshot,
        *,
        log_state: LogState,
        stop_reason: TaskStopReason | None = None,
        logs_confirmed: bool = True,
    ) -> bool:
        binding = self._task_bindings.get(command_id)
        if binding is None or self._session_factory is None:
            return False
        from cubeplex.models import SandboxCommand
        from cubeplex.services.background_tasks import BackgroundTaskService

        now = datetime.now(UTC)
        org_id, workspace_id = self._task_scope()
        async with self._session_factory() as session:
            command = await session.get(SandboxCommand, command_id)
            if command is None:
                raise LookupError("task command not found")
            service = BackgroundTaskService(
                session,
                org_id=org_id,
                workspace_id=workspace_id,
            )
            if stop_reason is not None:
                await service.request_task_stop(
                    task_id=binding.task_id,
                    reason=stop_reason,
                    now=now,
                )
            await service.record_observation(
                task_id=binding.task_id,
                owner_token=binding.owner_token,
                snapshot=snapshot,
                log_state=log_state,
                expected_log_cursor=command.log_cursor,
                confirmed_log_cursor=snapshot.log_cursor if logs_confirmed else None,
                now=now,
            )
            await service.defer_owner(
                task_id=binding.task_id,
                owner_token=binding.owner_token,
                now=now,
                retry_at=now + timedelta(seconds=1),
            )
            await session.commit()
        return True

    async def _persist_foreground(
        self,
        command_id: str,
        snapshot: ProcessSnapshot,
        logs_confirmed: bool,
    ) -> None:
        if await self._persist_task_observation(
            command_id,
            snapshot,
            log_state="complete" if logs_confirmed else "retrying",
            logs_confirmed=logs_confirmed,
        ):
            return
        await self._persist_discard(command_id)

    async def _persist_background_terminal(
        self,
        command_id: str,
        snapshot: ProcessSnapshot,
        logs_confirmed: bool,
        notify: bool,
    ) -> None:
        if await self._persist_task_observation(
            command_id,
            snapshot,
            log_state="complete" if logs_confirmed else "retrying",
            logs_confirmed=logs_confirmed,
        ):
            return
        if not logs_confirmed:
            return
        await self._persist_terminal(
            command_id,
            status=snapshot.status,
            exit_code=snapshot.exit_code,
            notify=notify and snapshot.status != "killed",
        )

    async def _kill_persisted_command(self, command_id: str) -> bool:
        if self.conversation_id is None:
            return False
        async with self._command_repo_ctx() as repo:
            if repo is None:
                return False
            row = await repo.get(command_id)
            if row is None or row.conversation_id != self.conversation_id:
                return False
            from cubeplex.models import UserSandbox
            from cubeplex.models.sandbox_command import SandboxCommandStatus
            from cubeplex.sandbox.command_coordinator import kill_command

            sandbox_row = await repo.session.get(UserSandbox, row.user_sandbox_id)
            if sandbox_row is None or sandbox_row.sandbox_id != self.sandbox.id:
                return False
            if row.status not in (
                SandboxCommandStatus.starting.value,
                SandboxCommandStatus.running.value,
            ):
                return False
            if row.task_id is not None:
                from cubeplex.models.background_task import TaskStopReason
                from cubeplex.services.background_tasks import BackgroundTaskService

                org_id, workspace_id = self._task_scope()
                await BackgroundTaskService(
                    repo.session,
                    org_id=org_id,
                    workspace_id=workspace_id,
                ).request_task_stop(
                    task_id=row.task_id,
                    reason=TaskStopReason.user_stop,
                    now=datetime.now(UTC),
                )
                await repo.session.commit()
                return True

            async def _current_sandbox(_row: object) -> Sandbox:
                return self.sandbox

            return await kill_command(repo.session, row, get_sandbox=_current_sandbox)

    async def _persist_terminal(
        self,
        command_id: str,
        *,
        status: str,
        exit_code: int | None,
        notify: bool,
        delivered: bool = False,
    ) -> None:
        if await self._persist_task_observation(
            command_id,
            ProcessSnapshot(
                status="killed" if status == "killed" else "exited",
                exit_code=exit_code,
            ),
            log_state="complete",
        ):
            return
        from cubeplex.models.sandbox_command import SandboxCommandNoticeState

        if delivered:
            notice = SandboxCommandNoticeState.delivered.value
        elif notify:
            notice = SandboxCommandNoticeState.pending.value
        else:
            notice = SandboxCommandNoticeState.none.value
        async with self._command_repo_ctx() as repo:
            if repo is None:
                return
            await repo.mark_terminal(
                command_id,
                status=status,
                exit_code=exit_code,
                finished_at=datetime.now(UTC),
                notice_state=notice,
            )

    async def _renew_live_leases(self) -> None:
        if not self._live_commands:
            return
        from cubeplex.sandbox.command_coordinator import COMMAND_LEASE_SECONDS

        async with self._command_repo_ctx() as repo:
            if repo is None:
                return
            await repo.renew_owner(
                list(self._live_commands),
                owner_id=self._owner_id,
                owner_until=datetime.now(UTC) + timedelta(seconds=COMMAND_LEASE_SECONDS),
            )

    def _ensure_lease_task(self) -> None:
        if self._lease_task is None or self._lease_task.done():
            self._lease_task = asyncio.create_task(self._lease_loop())

    async def _lease_loop(self) -> None:
        try:
            while self._live_commands:
                await asyncio.sleep(_COMMAND_LEASE_SECONDS / 3)
                try:
                    await self._renew_live_leases()
                except Exception:
                    logger.exception("sandbox command lease renew failed")
        except asyncio.CancelledError:
            return

    @asynccontextmanager
    async def _command_repo_ctx(self) -> AsyncIterator[Any]:
        from cubeplex.repositories.sandbox_command import SandboxCommandRepository

        if self._session_factory is None or self.org_id is None or self.workspace_id is None:
            yield None
            return
        async with self._session_factory() as session:
            yield SandboxCommandRepository(
                session, org_id=self.org_id, workspace_id=self.workspace_id
            )

    async def before_tool_call(
        self,
        ctx: BeforeToolCallContext,
        *,
        signal: asyncio.Event | None = None,
    ) -> BeforeToolCallResult | None:
        """Enforce command rules before an execute or monitor tool runs.

        Shell commands share one policy boundary. deny → block; confirm → pause
        on the HITL channel
        (approve runs it, deny/timeout/cancel block it); edit is rejected.
        Because this runs before the tool body, a blocked command never reaches
        ``sandbox.execute`` — no sandbox side effects, TTL clock untouched.
        """
        tool_name = getattr(ctx.tool_call, "name", None)
        if tool_name not in ("execute", "monitor"):
            return None
        if not self.command_rules:
            return None

        raw_args = ctx.args
        if isinstance(raw_args, (_ExecuteArgs, _MonitorArgs)):
            command = raw_args.command
        elif isinstance(raw_args, dict):
            command_value = raw_args.get("command")
            if not isinstance(command_value, str):
                return None
            command = command_value
        else:
            return None

        action, pattern = evaluate_command(command, self.command_rules)
        if action == "allow":
            return None
        if action == "deny":
            return BeforeToolCallResult(
                block=True,
                reason=(f"command blocked by org policy: {pattern}\n{POLICY_DENY_NUDGE}"),
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
                tool_name=tool_name,
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
        ctx: AgentContext,
        signal: asyncio.Event | None = None,
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
