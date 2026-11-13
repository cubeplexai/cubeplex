"""CubePod implementation of the Sandbox base class."""

from __future__ import annotations

import asyncio
import base64
import inspect
import re
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from cubepod import (
    AsyncCubePod,
    CubePodFileTooLargeError,
    SandboxInfo,
)
from cubepod import (
    FileEntry as SDKFileEntry,
)
from loguru import logger

from cubeplex.sandbox.base import (
    BrowserEndpoint,
    ExecuteResult,
    ProcessHandle,
    ProcessHandleNotFoundError,
    ProcessOutput,
    ProcessSnapshot,
    Sandbox,
    SandboxError,
    SandboxInstanceGoneError,
)
from cubeplex.sandbox.panel_token import (
    get_panel_base_url,
    get_panel_secret,
    sign_panel_token,
)

_TERMINAL_ENV_FILE = "/run/cubeplex/sandbox-env.sh"
_POSIX_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STREAM_POLL_INTERVAL = 0.05

FileTooLargeError = CubePodFileTooLargeError


@dataclass(frozen=True)
class CubePodFileEntry:
    """Normalized directory entry returned by CubePod file listing."""

    name: str
    path: str
    is_dir: bool
    size: int
    mode: str = "0644"
    modified_at: datetime | None = None


def _response_error_detail(resp: httpx.Response) -> str:
    try:
        return resp.text
    except httpx.ResponseNotRead:
        return resp.reason_phrase or ""


@asynccontextmanager
async def translate_cubepod_errors(
    *,
    sandbox_id: str | None = None,
    command_id: str | None = None,
    file_path: str | None = None,
    check_sandbox_alive: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[None]:
    """Translate ``cubepod`` SDK ``httpx`` exceptions into CubePlex sandbox errors."""
    try:
        yield
    except httpx.HTTPStatusError as exc:
        resp = exc.response
        detail = _response_error_detail(resp)
        if resp.status_code == 404:
            if file_path is not None:
                raise FileNotFoundError(file_path) from exc
            if command_id is not None and sandbox_id is not None:
                if "sandbox not found" in detail.lower():
                    raise SandboxInstanceGoneError(sandbox_id) from exc
                if check_sandbox_alive is not None and not await check_sandbox_alive():
                    raise SandboxInstanceGoneError(sandbox_id) from exc
                raise ProcessHandleNotFoundError(command_id) from exc
            if sandbox_id is not None:
                raise SandboxInstanceGoneError(sandbox_id) from exc
        raise SandboxError(f"CubePod API error {resp.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise SandboxError(str(exc)) from exc


async def connect_cubepod_sandbox(client: AsyncCubePod, sandbox_id: str) -> SandboxInfo:
    """Connect/resume a CubePod sandbox and reject terminal states."""
    async with translate_cubepod_errors(sandbox_id=sandbox_id):
        info = await client.connect(sandbox_id)
    if info.is_terminal:
        raise SandboxInstanceGoneError(sandbox_id)
    return info


async def get_cubepod_sandbox(client: AsyncCubePod, sandbox_id: str) -> SandboxInfo:
    """Fetch CubePod sandbox info and reject terminal states."""
    async with translate_cubepod_errors(sandbox_id=sandbox_id):
        info = await client.get(sandbox_id)
    if info.is_terminal:
        raise SandboxInstanceGoneError(sandbox_id)
    return info


def _snapshot_status(
    running: bool, exit_code: int | None
) -> Literal["running", "exited", "killed"]:
    if running:
        return "running"
    if exit_code in (-9, -15, 137, 143):
        return "killed"
    return "exited"


def _parse_modified_at(raw: str | datetime | None) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _to_file_entry(entry: SDKFileEntry) -> CubePodFileEntry:
    return CubePodFileEntry(
        name=entry.name,
        path=entry.path,
        is_dir=entry.is_dir,
        size=entry.size,
        mode=entry.mode,
        modified_at=_parse_modified_at(entry.modified_at),
    )


class CubePodSandbox(Sandbox):
    """Sandbox backed by a CubePod MicroVM via the official ``cubepod`` SDK."""

    def __init__(
        self,
        *,
        sandbox_id: str,
        client: AsyncCubePod,
        owns_client: bool = True,
        workdir: str = "/workspace",
        run_uid: int | None = 1000,
        run_gid: int | None = 1000,
        run_user: str | None = "cubeplex",
    ) -> None:
        self._sandbox_id = sandbox_id
        self._client = client
        self._owns_client = owns_client
        self._workdir = workdir
        self._run_env: dict[str, str] = {}
        self._run_uid = run_uid
        self._run_gid = run_gid
        self._run_user = run_user
        self._browser_port = self.BROWSER_PORT

    @property
    def id(self) -> str:
        return self._sandbox_id

    @property
    def workdir(self) -> str:
        return self._workdir

    def set_run_env(self, env: dict[str, str]) -> None:
        """Replace the run-level env dict injected into every execute call."""
        self._run_env = dict(env)

    def _uid_gid_for_call(self, as_root: bool) -> tuple[int | None, int | None]:
        if as_root:
            return (0, 0)
        if self._run_uid is not None:
            gid = self._run_gid if self._run_gid is not None else self._run_uid
            return (self._run_uid, gid)
        return (None, None)

    async def _is_sandbox_alive(self) -> bool:
        async with translate_cubepod_errors():
            try:
                info = await self._client.get(self._sandbox_id)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return False
                raise
        return not info.is_terminal

    def _translate(
        self,
        *,
        command_id: str | None = None,
        file_path: str | None = None,
    ) -> AbstractAsyncContextManager[None]:
        return translate_cubepod_errors(
            sandbox_id=self._sandbox_id,
            command_id=command_id,
            file_path=file_path,
            check_sandbox_alive=self._is_sandbox_alive if command_id is not None else None,
        )

    def _invoke_on_chunk(self, on_chunk: Callable[[str], None], text: str) -> None:
        if not text:
            return
        try:
            on_chunk(text)
        except Exception:
            logger.exception("CubePodSandbox on_chunk failed")

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ExecuteResult:
        merged = {**self._run_env, **(envs or {})}
        uid_val, gid_val = self._uid_gid_for_call(as_root)
        cmd_envs = merged or None

        if on_chunk is None:

            async def _run_fg() -> ExecuteResult:
                async with self._translate():
                    res = await self._client.execute(
                        self._sandbox_id,
                        command,
                        workdir=self._workdir,
                        envs=cmd_envs,
                        uid=uid_val,
                        gid=gid_val,
                    )
                return ExecuteResult(output=res.output, exit_code=res.exit_code)

            try:
                if timeout is not None:
                    return await asyncio.wait_for(_run_fg(), timeout=timeout)
                return await _run_fg()
            except TimeoutError:
                return ExecuteResult(output="[timeout]", exit_code=-1)

        cmd_id: str | None = None

        async def _run_streaming() -> ExecuteResult:
            nonlocal cmd_id
            async with self._translate():
                started = await self._client.start_command(
                    self._sandbox_id,
                    command,
                    workdir=self._workdir,
                    envs=cmd_envs,
                    uid=uid_val,
                    gid=gid_val,
                )
            cmd_id = started.id
            output_parts: list[str] = []
            if started.output:
                output_parts.append(started.output)
                self._invoke_on_chunk(on_chunk, started.output)
            cursor = started.cursor
            if not started.running:
                return ExecuteResult(
                    output="".join(output_parts),
                    exit_code=started.exit_code,
                )
            while True:
                async with self._translate(command_id=cmd_id):
                    logs = await self._client.poll_command(
                        self._sandbox_id,
                        cmd_id,
                        cursor=cursor,
                    )
                if logs.content:
                    output_parts.append(logs.content)
                    self._invoke_on_chunk(on_chunk, logs.content)
                cursor = logs.cursor
                if not logs.running:
                    return ExecuteResult(
                        output="".join(output_parts),
                        exit_code=logs.exit_code,
                    )
                await asyncio.sleep(_STREAM_POLL_INTERVAL)

        try:
            if timeout is not None:
                return await asyncio.wait_for(_run_streaming(), timeout=timeout)
            return await _run_streaming()
        except TimeoutError:
            if cmd_id:
                try:
                    await self._client.kill_command(self._sandbox_id, cmd_id)
                except Exception:
                    pass
            return ExecuteResult(output="[timeout]", exit_code=-1)

    def supports_background(self) -> bool:
        return True

    def supports_background_reconnect(self) -> bool:
        return True

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
        del provider_timeout
        merged = {**self._run_env, **(envs or {})}
        uid_val, gid_val = self._uid_gid_for_call(as_root)
        async with self._translate():
            info = await self._client.start_command(
                self._sandbox_id,
                command,
                workdir=self._workdir,
                envs=merged or None,
                uid=uid_val,
                gid=gid_val,
            )
        if on_started is not None:
            maybe_awaitable = on_started(info.id)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        if on_chunk is not None and info.output:
            self._invoke_on_chunk(on_chunk, info.output)
        deadline_at = (
            datetime.now(UTC) + timedelta(seconds=timeout) if timeout is not None else None
        )
        return ProcessHandle(
            command_id=info.id,
            provider_ref=info.id,
            log_cursor=str(info.cursor),
            deadline_at=deadline_at,
        )

    async def observe(self, handle: ProcessHandle) -> ProcessSnapshot:
        async with self._translate(command_id=handle.provider_ref):
            info = await self._client.get_command_status(self._sandbox_id, handle.provider_ref)
        return ProcessSnapshot(
            status=_snapshot_status(info.running, info.exit_code),
            exit_code=info.exit_code,
            new_output="",
            log_cursor=handle.log_cursor,
        )

    async def poll(self, handle: ProcessHandle) -> ProcessSnapshot:
        cursor = int(handle.log_cursor) if handle.log_cursor is not None else 0
        async with self._translate(command_id=handle.provider_ref):
            logs = await self._client.poll_command(
                self._sandbox_id,
                handle.provider_ref,
                cursor=cursor,
            )
        return ProcessSnapshot(
            status=_snapshot_status(logs.running, logs.exit_code),
            exit_code=logs.exit_code,
            new_output=logs.content,
            log_cursor=str(logs.cursor),
        )

    async def read_output(self, handle: ProcessHandle) -> ProcessOutput:
        snapshot = await self.poll(handle)
        return ProcessOutput(
            new_output=snapshot.new_output,
            log_cursor=snapshot.log_cursor,
        )

    async def kill(self, handle: ProcessHandle) -> None:
        async with self._translate(command_id=handle.provider_ref):
            await self._client.kill_command(self._sandbox_id, handle.provider_ref)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        uid_val: int | None = None
        gid_val: int | None = None
        if self._run_uid is not None:
            uid_val = self._run_uid
            gid_val = self._run_gid if self._run_gid is not None else self._run_uid
        async with self._translate():
            for path, content in files:
                await self._client.write_file(
                    self._sandbox_id,
                    path,
                    content,
                    uid=uid_val,
                    gid=gid_val,
                    mode="0644",
                )

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        results: list[tuple[str, bytes]] = []
        for path in paths:
            async with self._translate(file_path=path):
                data = await self._client.read_file(self._sandbox_id, path)
            results.append((path, data))
        return results

    def supports_archive_sync(self) -> bool:
        return True

    async def archive_sync(
        self,
        archive: bytes,
        *,
        dest: str = "/workspace",
        remove_paths: Sequence[str] | None = None,
    ) -> None:
        uid_val: int | None = None
        gid_val: int | None = None
        if self._run_uid is not None:
            uid_val = self._run_uid
            gid_val = self._run_gid if self._run_gid is not None else self._run_uid
        async with self._translate():
            await self._client.archive_sync(
                self._sandbox_id,
                archive,
                dest=dest,
                uid=uid_val,
                gid=gid_val,
                remove_paths=remove_paths,
            )

    async def list_directory(self, path: str) -> list[CubePodFileEntry]:
        async with self._translate():
            entries = await self._client.list_files(self._sandbox_id, path)
        return [_to_file_entry(entry) for entry in entries]

    async def read_text_file(self, path: str, *, max_bytes: int | None = None) -> str:
        async with self._translate(file_path=path):
            return await self._client.read_text_file(
                self._sandbox_id,
                path,
                max_bytes=max_bytes,
            )

    async def stream_file_bytes(self, path: str) -> AsyncIterator[bytes]:
        async with self._translate(file_path=path):
            raw_stream = await self._client.stream_file(self._sandbox_id, path)

        async def _iter() -> AsyncIterator[bytes]:
            aclose = getattr(raw_stream, "aclose", None)
            try:
                async for chunk in raw_stream:
                    yield chunk
            except httpx.HTTPError as exc:
                raise SandboxError(str(exc)) from exc
            finally:
                if aclose is not None:
                    await aclose()

        return _iter()

    def supports_pause(self) -> bool:
        return True

    async def pause(self) -> None:
        async with self._translate():
            await self._client.pause(self._sandbox_id)

    async def renew(self, timeout_seconds: int) -> None:
        async with self._translate():
            await self._client.renew_timeout(self._sandbox_id, timeout_seconds)

    async def acquire_lease(self, lease_seconds: int) -> None:
        async with self._translate():
            await self._client.acquire_lease(self._sandbox_id, lease_seconds)

    @classmethod
    async def connect_or_resume(  # type: ignore[override]
        cls,
        sandbox_id: str,
        *,
        client: AsyncCubePod | None = None,
        domain: str | None = None,
        api_key: str | None = None,
        request_timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        workdir: str = "/workspace",
        run_uid: int | None = 1000,
        run_gid: int | None = 1000,
        run_user: str | None = "cubeplex",
        **_: object,
    ) -> CubePodSandbox:
        owns_client = client is None
        if client is None:
            if not domain:
                raise SandboxError("CubePod domain is required to connect")
            client = AsyncCubePod(
                base_url=domain,
                api_key=api_key,
                timeout=request_timeout,
                transport=transport,
            )
        try:
            await connect_cubepod_sandbox(client, sandbox_id)
        except Exception:
            if owns_client:
                await client.close()
            raise
        return cls(
            sandbox_id=sandbox_id,
            client=client,
            owns_client=owns_client,
            workdir=workdir,
            run_uid=run_uid,
            run_gid=run_gid,
            run_user=run_user,
        )

    def supports_reset_rootfs(self) -> bool:
        return True

    async def reset_rootfs(self) -> None:
        async with self._translate():
            await self._client.reset_rootfs(self._sandbox_id)

    def supports_fork(self) -> bool:
        return True

    async def fork(
        self,
        *,
        volume_key: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> CubePodSandbox:
        async with self._translate():
            info = await self._client.fork(
                self._sandbox_id,
                volume_key=volume_key,
                metadata=metadata,
            )
        if not info.sandbox_id:
            raise SandboxError("CubePod fork response missing sandboxID")
        return CubePodSandbox(
            sandbox_id=info.sandbox_id,
            client=self._client,
            owns_client=False,
            workdir=self._workdir,
            run_uid=self._run_uid,
            run_gid=self._run_gid,
            run_user=self._run_user,
        )

    async def ensure_workspace_owner(self) -> None:
        # podd-init mounts /workspace owned by 1000:1000.
        return

    async def start_browser(self) -> None:
        await self.install_browser_tab_follow()
        probe = await self.execute(
            "test -x /usr/local/bin/cubepod-desktop-ctl",
            timeout=15,
            as_root=False,
        )
        if probe.exit_code == 0:
            res = await self.execute(
                "/usr/local/bin/cubepod-desktop-ctl start",
                timeout=120,
                as_root=False,
            )
            if res.exit_code not in (0, None):
                raise SandboxError(f"failed to start sandbox browser: {res.output}")
            port_probe = await self.execute(
                "(echo >/dev/tcp/127.0.0.1/8080) 2>/dev/null",
                timeout=10,
                as_root=False,
            )
            self._browser_port = 8080 if port_probe.exit_code == 0 else 6080
            return

        res = await self.execute(
            "/usr/local/bin/start-browser.sh",
            timeout=120,
            as_root=True,
        )
        if res.exit_code not in (0, None):
            raise SandboxError(f"failed to start sandbox browser: {res.output}")
        self._browser_port = self.BROWSER_PORT

    def _panel_endpoint(self, port: int, expires_in: int) -> BrowserEndpoint:
        base = get_panel_base_url()
        if not base:
            raise SandboxError(
                "sandbox panel is unavailable: backend public URL (api.public_url) is not configured"
            )
        token = sign_panel_token(
            sandbox_id=self._sandbox_id,
            port=port,
            secret=get_panel_secret(),
            ttl=timedelta(seconds=expires_in),
        )
        return BrowserEndpoint(url=f"{base}/sandbox-panel/{token}/", headers={})

    async def get_browser_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        return self._panel_endpoint(self._browser_port, expires_in)

    async def get_terminal_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        return self._panel_endpoint(self.TERMINAL_PORT, expires_in)

    async def _write_terminal_env(self) -> None:
        lines = [
            f"export {name}={shlex.quote(value)}"
            for name, value in sorted(self._run_env.items())
            if _POSIX_NAME_RE.fullmatch(name)
        ]
        payload = base64.b64encode(("\n".join(lines) + "\n").encode()).decode()
        directory = _TERMINAL_ENV_FILE.rsplit("/", 1)[0]
        result = await self.execute(
            f"mkdir -p {directory}"
            f" && tmp=$(mktemp {directory}/.sandbox-env.XXXXXX)"
            " && trap 'rm -f \"$tmp\"' EXIT"
            f' && printf %s {shlex.quote(payload)} | base64 -d > "$tmp"'
            ' && chmod 0644 "$tmp"'
            f' && mv -f "$tmp" {_TERMINAL_ENV_FILE}',
            timeout=30,
            as_root=True,
        )
        if result.exit_code not in (0, None):
            raise SandboxError(f"failed to write sandbox terminal env: {result.output}")

    async def start_terminal(self) -> None:
        await self._write_terminal_env()
        if self._run_uid is not None:
            gid = self._run_gid if self._run_gid is not None else self._run_uid
            shell = f"setpriv --reuid={self._run_uid} --regid={gid} --clear-groups -- bash"
        elif self._run_user:
            shell = f"runuser -u {self._run_user} -- bash"
        else:
            shell = "bash"
        result = await self.execute(
            "start-stop-daemon --start --oknodo --background"
            " --make-pidfile --pidfile /tmp/ttyd.pid"
            f" --exec /usr/bin/ttyd -- -p 7681 -W -w /workspace {shell}"
            " && sleep 1",
            timeout=30,
            as_root=True,
        )
        if result.exit_code not in (0, None):
            raise SandboxError(f"failed to start sandbox terminal: {result.output}")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()
