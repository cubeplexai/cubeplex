"""CubePod sandbox provider and handle driver."""

from __future__ import annotations

import asyncio
import inspect
import posixpath
import threading
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from cubepod import (
    AsyncCubePod,
    CubePodFileTooLargeError,
    NetworkEgressRule,
    NetworkPolicySpec,
    SandboxInfo,
)
from cubepod import (
    FileEntry as CubePodFileEntry,
)
from loguru import logger

from cubeplex.config import config
from cubeplex.sandbox.base import (
    ExecuteResult,
    FileEntry,
    ProcessHandle,
    ProcessHandleNotFoundError,
    ProcessOutput,
    ProcessSnapshot,
    SandboxCapabilities,
    SandboxCapabilityUnsupportedError,
    SandboxConflictError,
    SandboxError,
    SandboxFileTooLargeError,
    SandboxInstanceGoneError,
)
from cubeplex.sandbox.posix import PosixSandboxMixin
from cubeplex.sandbox.provider import (
    PanelUpstream,
    ProviderInstanceState,
    RunIdentity,
    SandboxCreateSpec,
    SandboxProvider,
    configured_text,
    default_run_identity,
    validate_optional_setting,
    validate_required_setting,
)
from cubeplex.sandbox_policy.rules import SandboxNetworkPolicy

_CUBEPOD_CAPABILITIES = SandboxCapabilities(
    background=True,
    reconnect=True,
    persistent_volume=True,
    pause=True,
    reset_rootfs=True,
    terminal=True,
    browser=True,
    egress_proxy=True,
    network_policy=True,
)

_TERMINAL_STATES = frozenset({"failed", "terminated", "stopped", "killed", "succeed"})
_EPOCH_UTC = datetime.fromtimestamp(0, tz=UTC)


def _normalize_base_url(raw: str) -> str:
    trimmed = raw.strip().rstrip("/")
    if not trimmed:
        return ""
    if not trimmed.startswith(("http://", "https://")):
        return f"http://{trimmed}"
    return trimmed


def _state_to_provider_state(state: str) -> ProviderInstanceState:
    normalized = state.strip().lower()
    if normalized == "running":
        return ProviderInstanceState.RUNNING
    if normalized == "paused":
        return ProviderInstanceState.PAUSED
    if normalized == "failed":
        return ProviderInstanceState.FAILED
    if normalized in _TERMINAL_STATES:
        return ProviderInstanceState.TERMINATED
    return ProviderInstanceState.TRANSITIONAL


def _translate_lifecycle_error(exc: Exception, sandbox_id: str) -> SandboxError:
    if isinstance(exc, SandboxError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return SandboxInstanceGoneError(sandbox_id)
        if code == 409:
            return SandboxConflictError(str(exc))
        return SandboxError(str(exc))
    if isinstance(exc, (httpx.HTTPError, TimeoutError, OSError)):
        return SandboxError(str(exc))
    return SandboxError(str(exc))


def _to_cubepod_network_policy(policy: SandboxNetworkPolicy | None) -> NetworkPolicySpec | None:
    if policy is None:
        return None
    egress: list[NetworkEgressRule] = [
        {"action": rule.action, "target": rule.target} for rule in policy.egress
    ]
    return NetworkPolicySpec(defaultAction=policy.default_action, egress=egress)


def _parse_modified_at(raw: str | None) -> datetime:
    if not raw:
        return _EPOCH_UTC
    text = raw.strip()
    if not text:
        return _EPOCH_UTC
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return _EPOCH_UTC
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _entry_to_file_entry(entry: CubePodFileEntry, parent: str) -> FileEntry:
    raw_path = entry.path or posixpath.join(parent, entry.name)
    name = entry.name or posixpath.basename(raw_path.rstrip("/")) or raw_path
    return FileEntry(
        path=raw_path,
        name=name,
        is_dir=bool(entry.is_dir),
        size=int(entry.size),
        modified_at=_parse_modified_at(entry.modified_at),
    )


class CubePodSandbox(PosixSandboxMixin):
    """POSIX-capable sandbox backed by ``cubepod.AsyncCubePod``."""

    def __init__(
        self,
        *,
        client: AsyncCubePod,
        sandbox_id: str,
        workdir: str,
        user_sandbox_id: str | None = None,
        run_uid: int | None = 1000,
        run_gid: int | None = 1000,
        run_user: str | None = "cubeplex",
    ) -> None:
        self._client = client
        self._sandbox_id = sandbox_id
        self._workdir = workdir
        self._user_sandbox_id = user_sandbox_id
        self._run_uid = run_uid
        self._run_gid = run_gid
        self._run_user = run_user
        self._run_env: dict[str, str] = {}
        self._log_cursors: dict[str, int] = {}

    @property
    def id(self) -> str:
        return self._sandbox_id

    @property
    def workdir(self) -> str:
        return self._workdir

    @property
    def provider_name(self) -> str:
        return "cubepod"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return _CUBEPOD_CAPABILITIES

    @property
    def user_sandbox_id(self) -> str | None:
        return self._user_sandbox_id

    def set_run_env(self, env: dict[str, str]) -> None:
        self._run_env = dict(env)

    def _uid_gid(self, *, as_root: bool) -> tuple[int | None, int | None]:
        if as_root:
            return 0, 0
        return self._run_uid, self._run_gid

    def _cursor_for(self, handle: ProcessHandle) -> int:
        if handle.log_cursor is not None:
            try:
                return int(handle.log_cursor)
            except ValueError:
                return self._log_cursors.get(handle.provider_ref, 0)
        return self._log_cursors.get(handle.provider_ref, 0)

    async def _raise_if_instance_gone(self) -> None:
        try:
            info = await self._client.get(self._sandbox_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise SandboxInstanceGoneError(self._sandbox_id) from exc
            return
        except Exception:
            return
        if info.is_terminal or info.state.strip().lower() in _TERMINAL_STATES:
            raise SandboxInstanceGoneError(self._sandbox_id)

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
        uid, gid = self._uid_gid(as_root=as_root)
        try:
            res = await self._client.execute(
                self._sandbox_id,
                command,
                workdir=self._workdir,
                envs=merged or None,
                uid=uid,
                gid=gid,
                timeout=float(timeout) if timeout is not None else None,
            )
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise _translate_lifecycle_error(exc, self._sandbox_id) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            if timeout is None:
                raise _translate_lifecycle_error(exc, self._sandbox_id) from exc
            return ExecuteResult("[timeout]", -1)
        except (httpx.HTTPError, OSError) as exc:
            raise _translate_lifecycle_error(exc, self._sandbox_id) from exc

        if on_chunk is not None and res.output:
            try:
                on_chunk(res.output)
            except Exception:
                logger.exception("CubePod on_chunk failed")
        return ExecuteResult(output=res.output, exit_code=res.exit_code)

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
        del timeout, provider_timeout, on_chunk
        merged = {**self._run_env, **(envs or {})}
        uid, gid = self._uid_gid(as_root=as_root)
        try:
            info = await self._client.start_command(
                self._sandbox_id,
                command,
                workdir=self._workdir,
                envs=merged or None,
                uid=uid,
                gid=gid,
            )
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            await self._raise_if_instance_gone()
            raise _translate_lifecycle_error(exc, self._sandbox_id) from exc

        ref = str(info.id or "")
        self._log_cursors[ref] = int(info.cursor or 0)
        if on_started is not None and ref:
            maybe = on_started(ref)
            if inspect.isawaitable(maybe):
                await maybe
        return ProcessHandle(command_id="", provider_ref=ref)

    async def observe(self, handle: ProcessHandle) -> ProcessSnapshot:
        try:
            info = await self._client.get_command_status(self._sandbox_id, handle.provider_ref)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ProcessHandleNotFoundError(handle.provider_ref) from exc
            raise _translate_lifecycle_error(exc, self._sandbox_id) from exc
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise SandboxError(str(exc)) from exc
        return ProcessSnapshot(
            status="running" if info.running else "exited",
            exit_code=info.exit_code,
            log_cursor=str(info.cursor),
        )

    async def poll(self, handle: ProcessHandle) -> ProcessSnapshot:
        cursor = self._cursor_for(handle)
        try:
            logs = await self._client.poll_command(
                self._sandbox_id,
                handle.provider_ref,
                cursor=cursor,
            )
        except httpx.HTTPStatusError as exc:
            await self._raise_if_instance_gone()
            if exc.response.status_code == 404:
                raise ProcessHandleNotFoundError(handle.provider_ref) from exc
            raise _translate_lifecycle_error(exc, self._sandbox_id) from exc
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            await self._raise_if_instance_gone()
            raise SandboxError(str(exc)) from exc

        return ProcessSnapshot(
            status="running" if logs.running else "exited",
            exit_code=logs.exit_code,
            new_output=logs.content,
            log_cursor=str(logs.cursor),
        )

    async def read_output(self, handle: ProcessHandle) -> ProcessOutput:
        cursor = self._cursor_for(handle)
        try:
            logs = await self._client.poll_command(
                self._sandbox_id,
                handle.provider_ref,
                cursor=cursor,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ProcessHandleNotFoundError(handle.provider_ref) from exc
            raise _translate_lifecycle_error(exc, self._sandbox_id) from exc
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise SandboxError(str(exc)) from exc

        return ProcessOutput(new_output=logs.content, log_cursor=str(logs.cursor))

    async def acknowledge_output(self, handle: ProcessHandle, cursor: str) -> None:
        await super().acknowledge_output(handle, cursor)
        if handle.provider_ref:
            with suppress(ValueError):
                self._log_cursors[handle.provider_ref] = int(cursor)

    async def kill(self, handle: ProcessHandle) -> None:
        try:
            await self._client.kill_command(self._sandbox_id, handle.provider_ref)
        except httpx.HTTPStatusError as exc:
            await self._raise_if_instance_gone()
            if exc.response.status_code == 404:
                raise ProcessHandleNotFoundError(handle.provider_ref) from exc
            raise _translate_lifecycle_error(exc, self._sandbox_id) from exc
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise SandboxError(str(exc)) from exc

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        if not files:
            return
        try:
            for path, content in files:
                await self._client.write_file(
                    self._sandbox_id,
                    path,
                    content,
                    uid=self._run_uid,
                    gid=self._run_gid,
                )
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise _translate_lifecycle_error(exc, self._sandbox_id) from exc

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        results: list[tuple[str, bytes]] = []
        for path in paths:
            try:
                content = await self._client.read_file(self._sandbox_id, path)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise FileNotFoundError(path) from exc
                raise SandboxError(str(exc)) from exc
            except (httpx.HTTPError, TimeoutError, OSError) as exc:
                raise SandboxError(str(exc)) from exc
            results.append((path, content))
        return results

    async def list_dir(self, path: str) -> list[FileEntry]:
        try:
            entries = await self._client.list_files(self._sandbox_id, path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise FileNotFoundError(path) from exc
            raise SandboxError(str(exc)) from exc
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise SandboxError(str(exc)) from exc
        return [_entry_to_file_entry(entry, path) for entry in entries]

    async def stat_file(self, path: str) -> FileEntry:
        norm = posixpath.normpath(path) or "/"
        if norm == "/":
            await self.list_dir("/")
            return FileEntry(
                path="/",
                name="/",
                is_dir=True,
                size=0,
                modified_at=_EPOCH_UTC,
            )
        parent = posixpath.dirname(norm) or "/"
        base = posixpath.basename(norm)
        entries = await self.list_dir(parent)
        for entry in entries:
            if entry.path == norm or entry.name == base:
                return entry
        raise FileNotFoundError(path)

    async def read_text(self, path: str, *, max_bytes: int | None = None) -> str:
        try:
            return await self._client.read_text_file(
                self._sandbox_id,
                path,
                max_bytes=max_bytes,
            )
        except CubePodFileTooLargeError as exc:
            raise SandboxFileTooLargeError(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise FileNotFoundError(path) from exc
            raise SandboxError(str(exc)) from exc
        except (httpx.HTTPError, TimeoutError, UnicodeDecodeError, ValueError, OSError) as exc:
            raise SandboxError(str(exc)) from exc

    @asynccontextmanager
    async def stream_file(self, path: str) -> AsyncIterator[AsyncIterator[bytes]]:
        try:
            raw_stream = await self._client.stream_file(self._sandbox_id, path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise FileNotFoundError(path) from exc
            raise SandboxError(str(exc)) from exc
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise SandboxError(str(exc)) from exc

        async def _iter_chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in raw_stream:
                    yield chunk
            except (httpx.HTTPError, TimeoutError, OSError) as exc:
                raise SandboxError(str(exc)) from exc

        try:
            yield _iter_chunks()
        finally:
            aclose = getattr(raw_stream, "aclose", None)
            if callable(aclose):
                with suppress(Exception):
                    await aclose()

    async def sync_archive(
        self,
        tarball: bytes | None,
        *,
        dest: str,
        repush_names: Sequence[str] = (),
        remove_paths: Sequence[str] = (),
    ) -> None:
        removals = [*repush_names, *remove_paths]
        if not tarball and not removals:
            return
        try:
            await self._client.archive_sync(
                self._sandbox_id,
                tarball or b"",
                dest=dest,
                uid=self._run_uid,
                gid=self._run_gid,
                remove_paths=removals or None,
            )
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise SandboxError(f"CubePod archive_sync failed: {exc}") from exc

    async def close(self) -> None:
        # Sandbox handles share the provider's pooled AsyncCubePod client.
        return

    async def is_healthy(self) -> bool:
        try:
            info = await self._client.get(self._sandbox_id)
        except Exception:
            return False
        return info.state.strip().lower() == "running"

    async def pause(self) -> None:
        try:
            await self._client.pause(self._sandbox_id)
        except Exception as exc:
            raise _translate_lifecycle_error(exc, self._sandbox_id) from exc

    async def renew(self, ttl_seconds: int) -> None:
        try:
            await self._client.renew_timeout(self._sandbox_id, timeout_seconds=ttl_seconds)
        except Exception as exc:
            raise _translate_lifecycle_error(exc, self._sandbox_id) from exc


@dataclass
class _LoopClientState:
    lock: asyncio.Lock
    client: AsyncCubePod | None = None
    key: tuple[str, str, float] | None = None


class CubePodProvider(SandboxProvider):
    """Control-plane ``SandboxProvider`` backed by CubePod."""

    def __init__(
        self,
        *,
        client: AsyncCubePod | None = None,
        poll_interval: float = 0.2,
    ) -> None:
        self._injected_client = client
        self._poll_interval = poll_interval
        self._states_guard = threading.Lock()
        self._loop_states: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopClientState
        ] = weakref.WeakKeyDictionary()
        self._retired_clients: list[tuple[asyncio.AbstractEventLoop, AsyncCubePod]] = []

    @property
    def name(self) -> str:
        return "cubepod"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return _CUBEPOD_CAPABILITIES

    @property
    def default_workdir(self) -> str:
        return configured_text("sandbox.workdir") or "/workspace"

    @property
    def default_image(self) -> str | None:
        return configured_text("sandbox.cubepod.default_template") or None

    def validate_config(self) -> None:
        validate_required_setting(
            "sandbox.cubepod.base_url",
            "CUBEPLEX_SANDBOX__CUBEPOD__BASE_URL",
        )
        validate_required_setting(
            "sandbox.cubepod.default_template",
            "CUBEPLEX_SANDBOX__CUBEPOD__DEFAULT_TEMPLATE",
        )
        validate_optional_setting(
            "sandbox.cubepod.api_key",
            "CUBEPLEX_SANDBOX__CUBEPOD__API_KEY",
        )

    def _base_url(self) -> str:
        return _normalize_base_url(configured_text("sandbox.cubepod.base_url"))

    def _api_key(self) -> str:
        return configured_text("sandbox.cubepod.api_key")

    def _request_timeout_seconds(self) -> float:
        return float(config.get("sandbox.request_timeout", 1860.0))

    def _state_for_loop(self, loop: asyncio.AbstractEventLoop) -> _LoopClientState:
        with self._states_guard:
            state = self._loop_states.get(loop)
            if state is None:
                state = _LoopClientState(lock=asyncio.Lock())
                self._loop_states[loop] = state
            return state

    async def _get_client(self) -> AsyncCubePod:
        if self._injected_client is not None:
            return self._injected_client
        base_url = self._base_url()
        if not base_url:
            raise SandboxError("CubePod requires sandbox.cubepod.base_url")
        api_key = self._api_key()
        timeout = self._request_timeout_seconds()
        key = (base_url, api_key, timeout)

        loop = asyncio.get_running_loop()
        state = self._state_for_loop(loop)
        async with state.lock:
            if state.client is not None and state.key == key:
                return state.client
            if state.client is not None:
                with self._states_guard:
                    self._retired_clients.append((loop, state.client))
            state.client = AsyncCubePod(
                base_url=base_url,
                api_key=api_key or None,
                timeout=timeout,
                trust_env=False,
            )
            state.key = key
            return state.client

    def _make_handle(
        self,
        *,
        client: AsyncCubePod,
        sandbox_id: str,
        workdir: str | None = None,
        user_sandbox_id: str | None = None,
        run_identity: RunIdentity | None = None,
    ) -> CubePodSandbox:
        ident = run_identity or default_run_identity()
        return CubePodSandbox(
            client=client,
            sandbox_id=sandbox_id,
            workdir=workdir or self.default_workdir,
            user_sandbox_id=user_sandbox_id,
            run_uid=ident.uid,
            run_gid=ident.gid,
            run_user=ident.user,
        )

    async def _wait_until_running(
        self,
        client: AsyncCubePod,
        sandbox_id: str,
        *,
        timeout_seconds: float,
        operation: str,
    ) -> SandboxInfo:
        deadline = time.monotonic() + max(float(timeout_seconds), 0.1)
        while True:
            try:
                info = await client.get(sandbox_id)
            except Exception as exc:
                raise _translate_lifecycle_error(exc, sandbox_id) from exc
            state_lower = info.state.strip().lower()
            if state_lower == "running":
                return info
            if state_lower in _TERMINAL_STATES:
                raise SandboxError(
                    f"CubePod sandbox {sandbox_id} entered terminal state "
                    f"{info.state!r} during {operation}"
                )
            if time.monotonic() >= deadline:
                raise SandboxError(
                    f"Timed out waiting for CubePod sandbox {sandbox_id} to reach "
                    f"Running during {operation} (last state={info.state!r})"
                )
            await asyncio.sleep(self._poll_interval)

    async def create(
        self,
        spec: SandboxCreateSpec,
        *,
        on_created: Callable[[str], Awaitable[None]] | None = None,
    ) -> CubePodSandbox:
        client = await self._get_client()
        template_id = spec.image or self.default_image or "cubeplex-ubuntu24-desktop"
        warm_pool_ref = configured_text("sandbox.cubepod.warm_pool") or None
        resource: dict[str, str] | None = None
        if spec.resource_cpu or spec.resource_memory:
            resource = {}
            if spec.resource_cpu:
                resource["cpu"] = spec.resource_cpu
            if spec.resource_memory:
                resource["memory"] = spec.resource_memory

        try:
            info = await client.create(
                template_id,
                volume_key=spec.volume_key,
                metadata=dict(spec.metadata) if spec.metadata else None,
                network_policy=_to_cubepod_network_policy(spec.network_policy),
                timeout_seconds=spec.ttl_seconds,
                warm_pool_ref=warm_pool_ref,
                resource=resource,
            )
        except Exception as exc:
            raise _translate_lifecycle_error(exc, "") from exc

        sandbox_id = info.sandbox_id
        if not sandbox_id:
            raise SandboxError("CubePod create returned an empty sandbox_id")
        if on_created is not None:
            await on_created(sandbox_id)
        if info.state.strip().lower() != "running":
            await self._wait_until_running(
                client,
                sandbox_id,
                timeout_seconds=spec.ready_timeout_seconds,
                operation="create",
            )
        return self._make_handle(
            client=client,
            sandbox_id=sandbox_id,
            workdir=spec.workdir,
            user_sandbox_id=spec.user_sandbox_id,
            run_identity=spec.run_identity,
        )

    async def connect(
        self,
        sandbox_id: str,
        *,
        workdir: str | None = None,
        user_sandbox_id: str | None = None,
        run_identity: RunIdentity | None = None,
    ) -> CubePodSandbox:
        # Use GET instead of client.connect() so paused sandboxes are never auto-resumed.
        client = await self._get_client()
        try:
            info = await client.get(sandbox_id)
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc

        state_lower = info.state.strip().lower()
        if state_lower in _TERMINAL_STATES:
            raise SandboxInstanceGoneError(sandbox_id)
        if state_lower != "running":
            raise SandboxError(
                f"CubePod sandbox {sandbox_id} is in state {info.state!r}, expected Running"
            )
        return self._make_handle(
            client=client,
            sandbox_id=sandbox_id,
            workdir=workdir,
            user_sandbox_id=user_sandbox_id,
            run_identity=run_identity,
        )

    async def connect_observer(
        self,
        sandbox_id: str,
        *,
        timeout_seconds: int = 30,
    ) -> CubePodSandbox:
        if not self.capabilities.reconnect:
            raise SandboxCapabilityUnsupportedError("reconnect", self.name)
        client = await self._get_client()
        try:
            async with asyncio.timeout(timeout_seconds):
                info = await client.get(sandbox_id)
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError(f"could not observe sandbox instance {sandbox_id}: {exc}") from exc

        if info.state.strip().lower() != "running":
            raise SandboxError(
                f"could not observe sandbox instance {sandbox_id}: state is {info.state!r}"
            )
        return self._make_handle(client=client, sandbox_id=sandbox_id)

    async def resume(
        self,
        sandbox_id: str,
        *,
        workdir: str | None = None,
        user_sandbox_id: str | None = None,
        run_identity: RunIdentity | None = None,
    ) -> CubePodSandbox:
        client = await self._get_client()
        try:
            info = await client.connect(sandbox_id)
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc

        if info.state.strip().lower() != "running":
            resume_timeout = float(config.get("sandbox.resume_timeout", 60))
            await self._wait_until_running(
                client,
                sandbox_id,
                timeout_seconds=resume_timeout,
                operation="resume",
            )
        return self._make_handle(
            client=client,
            sandbox_id=sandbox_id,
            workdir=workdir,
            user_sandbox_id=user_sandbox_id,
            run_identity=run_identity,
        )

    async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
        client = await self._get_client()
        try:
            info = await client.get(sandbox_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise _translate_lifecycle_error(exc, sandbox_id) from exc
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc
        return _state_to_provider_state(info.state)

    async def pause(self, sandbox_id: str) -> None:
        client = await self._get_client()
        try:
            await client.pause(sandbox_id)
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc

    async def terminate(self, sandbox_id: str) -> None:
        client = await self._get_client()
        try:
            await client.delete(sandbox_id, delete_volumes=False)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return
            raise _translate_lifecycle_error(exc, sandbox_id) from exc
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc

    async def renew_ttl(self, sandbox_id: str, ttl_seconds: int) -> None:
        client = await self._get_client()
        try:
            await client.renew_timeout(sandbox_id, timeout_seconds=ttl_seconds)
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc

    async def reset_rootfs(self, sandbox_id: str) -> None:
        client = await self._get_client()
        try:
            info = await client.reset_rootfs(sandbox_id)
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc
        if info.state.strip().lower() != "running":
            reset_timeout = float(config.get("sandbox.resume_timeout", 60))
            await self._wait_until_running(
                client,
                sandbox_id,
                timeout_seconds=reset_timeout,
                operation="reset_rootfs",
            )

    def build_panel_upstream(
        self,
        *,
        sandbox_id: str,
        port: int,
        path: str,
        query: str,
    ) -> PanelUpstream:
        base_url = self._base_url()
        if not base_url:
            raise SandboxError("CubePod panel proxy requires sandbox.cubepod.base_url")
        parsed = urlsplit(base_url)
        http_scheme = parsed.scheme or "http"
        ws_scheme = "wss" if http_scheme == "https" else "ws"
        base_path = parsed.path.rstrip("/")
        host_header = f"{port}-{sandbox_id}.cubepod.local"
        clean_path = path.lstrip("/")
        qs = f"?{query}" if query else ""

        http_headers: dict[str, str] = {
            "Host": host_header,
            "accept-encoding": "identity",
        }
        ws_headers: dict[str, str] = {}
        api_key = self._api_key()
        if api_key:
            auth = f"Bearer {api_key}"
            http_headers["Authorization"] = auth
            ws_headers["Authorization"] = auth

        default_port = 443 if http_scheme == "https" else 80
        connect_host = parsed.hostname
        connect_port = parsed.port or default_port
        server_hostname = parsed.hostname if ws_scheme == "wss" else None

        return PanelUpstream(
            http_url=f"{base_url}/{clean_path}{qs}",
            http_headers=http_headers,
            ws_url=f"{ws_scheme}://{host_header}{base_path}/{clean_path}{qs}",
            ws_headers=ws_headers,
            ws_connect_host=connect_host,
            ws_connect_port=connect_port,
            ws_server_hostname=server_hostname,
        )

    async def aclose(self) -> None:
        with self._states_guard:
            entries: list[tuple[asyncio.AbstractEventLoop, AsyncCubePod]] = [
                (loop, state.client)
                for loop, state in list(self._loop_states.items())
                if state.client is not None
            ]
            entries.extend(self._retired_clients)
            self._loop_states.clear()
            self._retired_clients.clear()
            injected = self._injected_client
            self._injected_client = None

        current_loop = asyncio.get_running_loop()
        for client_loop, client in entries:
            if client_loop.is_closed() and client_loop is not current_loop:
                continue
            with suppress(Exception):
                await client.close()
        if injected is not None:
            with suppress(Exception):
                await injected.close()
