"""Local sandbox and LocalSandboxProvider using asyncio subprocesses — for dev/debug only."""

from __future__ import annotations

import asyncio
import inspect
import os
import signal
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from cubeplex.config import config
from cubeplex.sandbox.base import (
    ExecuteResult,
    FileEntry,
    ProcessHandle,
    ProcessOutput,
    ProcessSnapshot,
    Sandbox,
    SandboxCapabilities,
    SandboxError,
    SandboxFileTooLargeError,
    SandboxInstanceGoneError,
)
from cubeplex.sandbox.provider import (
    ProviderInstanceState,
    RunIdentity,
    SandboxCreateSpec,
    SandboxProvider,
)

_LOCAL_CAPABILITIES = SandboxCapabilities(
    background=True,
    reconnect=False,
    persistent_volume=True,
    pause=False,
    reset_rootfs=False,
    terminal=False,
    browser=False,
    egress_proxy=False,
    network_policy=False,
)


class _LocalBgProc:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.killed = False
        self.pump_task: asyncio.Task[None] | None = None
        self._buf = bytearray()
        self._base_cursor = 0
        self._lock = asyncio.Lock()

    async def pump(self) -> None:
        assert self.proc.stdout is not None
        while True:
            data = await self.proc.stdout.read(4096)
            if not data:
                break
            async with self._lock:
                self._buf.extend(data)

    async def read_from(self, cursor: int) -> tuple[str, int]:
        async with self._lock:
            end_cursor = self._base_cursor + len(self._buf)
            if cursor < self._base_cursor or cursor > end_cursor:
                raise SandboxError("local process output cursor is invalid")
            confirmed = cursor - self._base_cursor
            if confirmed:
                del self._buf[:confirmed]
                self._base_cursor = cursor
            text = bytes(self._buf).decode(errors="replace")
            return text, self._base_cursor + len(self._buf)

    async def acknowledge(self, cursor: int) -> None:
        async with self._lock:
            end_cursor = self._base_cursor + len(self._buf)
            if cursor < self._base_cursor or cursor > end_cursor:
                raise SandboxError("local process output cursor is invalid")
            confirmed = cursor - self._base_cursor
            if confirmed:
                del self._buf[:confirmed]
                self._base_cursor = cursor


def _emit_chunk(on_chunk: Callable[[str], None] | None, text: str) -> None:
    if on_chunk is None or not text:
        return
    try:
        on_chunk(text)
    except Exception:
        logger.exception("LocalSandbox on_chunk failed")


class LocalSandbox(Sandbox):
    """Sandbox backed by local asyncio subprocesses.

    Not suitable for production. Use for development and testing.
    """

    def __init__(
        self,
        *,
        workdir: str | None = None,
        sandbox_id: str | None = None,
        user_sandbox_id: str | None = None,
    ) -> None:
        self._id = sandbox_id or str(uuid.uuid4())
        self._workdir = workdir or os.getcwd()
        self._user_sandbox_id = user_sandbox_id
        self._bg: dict[str, _LocalBgProc] = {}

    @property
    def id(self) -> str:
        return self._id

    @property
    def workdir(self) -> str:
        return self._workdir

    @property
    def provider_name(self) -> str:
        return "local"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return _LOCAL_CAPABILITIES

    @property
    def user_sandbox_id(self) -> str | None:
        return self._user_sandbox_id

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ExecuteResult:
        # envs/as_root accepted for interface compatibility but not applied:
        # LocalSandbox runs in the host process environment and is not used
        # in production.
        del envs, as_root
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._workdir,
        )
        chunks: list[str] = []

        async def _pump() -> None:
            assert proc.stdout is not None
            while True:
                data = await proc.stdout.read(4096)
                if not data:
                    break
                text = data.decode(errors="replace")
                chunks.append(text)
                _emit_chunk(on_chunk, text)

        try:
            await asyncio.wait_for(_pump(), timeout=timeout)
            await proc.wait()
        except TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except Exception:
                pass
            return ExecuteResult(output="[timeout]", exit_code=-1)

        return ExecuteResult(
            output="".join(chunks),
            exit_code=proc.returncode,
        )

    def supports_background(self) -> bool:
        return True

    async def observe(self, handle: ProcessHandle) -> ProcessSnapshot:
        rec = self._bg.get(handle.provider_ref)
        if rec is None:
            raise SandboxError("local process reference is not available on this worker")
        code = rec.proc.returncode
        if code is None:
            return ProcessSnapshot(status="running")
        return ProcessSnapshot(
            status="killed" if rec.killed and code < 0 else "exited", exit_code=code
        )

    async def start(
        self,
        command: str,
        *,
        timeout: int | None = None,
        provider_timeout: bool = False,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk: Callable[[str], None] | None = None,
        on_started: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> ProcessHandle:
        del timeout, provider_timeout, envs, as_root, on_chunk
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._workdir,
            start_new_session=True,
        )
        rec = _LocalBgProc(proc)
        ref = str(id(rec))
        self._bg[ref] = rec
        rec.pump_task = asyncio.create_task(rec.pump())
        if on_started is not None:
            maybe = on_started(ref)
            if inspect.isawaitable(maybe):
                await maybe
        return ProcessHandle(command_id="", provider_ref=ref)

    async def poll(self, handle: ProcessHandle) -> ProcessSnapshot:
        status = await self.observe(handle)
        output = await self.read_output(handle)
        return ProcessSnapshot(
            status=status.status,
            exit_code=status.exit_code,
            new_output=output.new_output,
            log_cursor=output.log_cursor,
        )

    async def read_output(self, handle: ProcessHandle) -> ProcessOutput:
        rec = self._bg.get(handle.provider_ref)
        if rec is None:
            raise SandboxError("local process reference is not available on this worker")
        code = rec.proc.returncode
        if code is not None and rec.pump_task is not None:
            await rec.pump_task
        try:
            cursor = int(handle.log_cursor) if handle.log_cursor is not None else 0
        except ValueError as exc:
            raise SandboxError("local process output cursor is invalid") from exc
        new_output, next_cursor = await rec.read_from(cursor)
        return ProcessOutput(new_output=new_output, log_cursor=str(next_cursor))

    async def acknowledge_output(self, handle: ProcessHandle, cursor: str) -> None:
        rec = self._bg.get(handle.provider_ref)
        if rec is None:
            raise SandboxError("local process reference is not available on this worker")
        try:
            parsed_cursor = int(cursor)
        except ValueError as exc:
            raise SandboxError("local process output cursor is invalid") from exc
        await rec.acknowledge(parsed_cursor)
        handle.log_cursor = cursor

    async def kill(self, handle: ProcessHandle) -> None:
        rec = self._bg.get(handle.provider_ref)
        if rec is None:
            raise SandboxError("local process reference is not available on this worker")
        if rec.proc.returncode is not None:
            return
        pid = rec.proc.pid
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGKILL)
                rec.killed = True
            except ProcessLookupError:
                try:
                    rec.proc.kill()
                    rec.killed = True
                except ProcessLookupError:
                    pass
        else:
            try:
                rec.proc.kill()
                rec.killed = True
            except ProcessLookupError:
                pass
        await rec.proc.wait()

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        for path, content in files:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        result = []
        for path in paths:
            result.append((path, Path(path).read_bytes()))
        return result

    def _resolve_path(self, path: str) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        return Path(self._workdir) / candidate

    @staticmethod
    def _to_file_entry(target: Path) -> FileEntry:
        st = target.stat()
        is_dir = target.is_dir()
        return FileEntry(
            path=str(target),
            name=target.name or str(target),
            is_dir=is_dir,
            size=0 if is_dir else int(st.st_size),
            modified_at=datetime.fromtimestamp(st.st_mtime, tz=UTC),
        )

    async def list_dir(self, path: str) -> list[FileEntry]:
        target = self._resolve_path(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if not target.is_dir():
            raise SandboxError(f"path is not a directory: {path}")
        children = sorted(target.iterdir(), key=lambda p: p.name)
        return [self._to_file_entry(child) for child in children]

    async def stat_file(self, path: str) -> FileEntry:
        target = self._resolve_path(path)
        if not target.exists():
            raise FileNotFoundError(path)
        return self._to_file_entry(target)

    async def read_text(self, path: str, *, max_bytes: int | None = None) -> str:
        target = self._resolve_path(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_dir():
            raise SandboxError(f"path is a directory: {path}")
        if max_bytes is not None and target.stat().st_size > max_bytes:
            raise SandboxFileTooLargeError(f"{path} exceeds max_bytes={max_bytes}")
        data = await asyncio.to_thread(target.read_bytes)
        if max_bytes is not None and len(data) > max_bytes:
            raise SandboxFileTooLargeError(f"{path} exceeds max_bytes={max_bytes}")
        return data.decode("utf-8", errors="replace")

    @asynccontextmanager
    async def stream_file(self, path: str) -> AsyncIterator[AsyncIterator[bytes]]:
        target = self._resolve_path(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_dir():
            raise SandboxError(f"path is a directory: {path}")

        async def _iter_chunks() -> AsyncIterator[bytes]:
            with target.open("rb") as handle:
                while True:
                    chunk = await asyncio.to_thread(handle.read, 65536)
                    if not chunk:
                        break
                    yield chunk

        yield _iter_chunks()

    async def close(self) -> None:
        return None


class LocalSandboxProvider(SandboxProvider):
    """In-process ``SandboxProvider`` for local development and tests."""

    def __init__(self, *, base_workdir: str | None = None) -> None:
        self._base_workdir = base_workdir
        self._instances: dict[str, LocalSandbox] = {}

    @property
    def name(self) -> str:
        return "local"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return _LOCAL_CAPABILITIES

    @property
    def default_workdir(self) -> str:
        if self._base_workdir:
            return self._base_workdir
        configured = str(config.get("sandbox.local.workdir", "") or "").strip()
        return configured or "/tmp/cubeplex_sandbox"

    @property
    def default_image(self) -> str | None:
        return "local"

    async def create(
        self,
        spec: SandboxCreateSpec,
        *,
        on_created: Callable[[str], Awaitable[None]] | None = None,
    ) -> LocalSandbox:
        base = Path(spec.workdir or self.default_workdir)
        target_dir = (base / spec.volume_key) if spec.volume_key else base
        target_dir.mkdir(parents=True, exist_ok=True)
        sandbox = LocalSandbox(
            workdir=str(target_dir),
            user_sandbox_id=spec.user_sandbox_id,
        )
        self._instances[sandbox.id] = sandbox
        if on_created is not None:
            await on_created(sandbox.id)
        return sandbox

    async def connect(
        self,
        sandbox_id: str,
        *,
        workdir: str | None = None,
        user_sandbox_id: str | None = None,
        run_identity: RunIdentity | None = None,
    ) -> LocalSandbox:
        del run_identity
        existing = self._instances.get(sandbox_id)
        if existing is None:
            raise SandboxInstanceGoneError(sandbox_id)
        effective_workdir = (
            existing.workdir if workdir is None or workdir == self.default_workdir else workdir
        )
        return LocalSandbox(
            workdir=effective_workdir,
            sandbox_id=existing.id,
            user_sandbox_id=(
                user_sandbox_id if user_sandbox_id is not None else existing.user_sandbox_id
            ),
        )

    async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
        if sandbox_id in self._instances:
            return ProviderInstanceState.RUNNING
        return None

    async def terminate(self, sandbox_id: str) -> None:
        self._instances.pop(sandbox_id, None)

    async def aclose(self) -> None:
        self._instances.clear()
