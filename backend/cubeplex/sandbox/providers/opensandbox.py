"""OpenSandbox implementation of Sandbox and SandboxProvider."""

from __future__ import annotations

import asyncio
import inspect
import posixpath
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import UTC, timedelta
from typing import Any

import opensandbox
from loguru import logger
from opensandbox.adapters import command_adapter as _cmd_adapter
from opensandbox.adapters.converter.command_execution import (
    decode_sse_event_data as _decode_sse_event_data,
)
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxApiException as _ProviderApiError
from opensandbox.exceptions import SandboxException as _ProviderError
from opensandbox.models.execd import ExecutionHandlers, RunCommandOpts
from opensandbox.models.filesystem import DirectoryListEntry, EntryInfo
from opensandbox.models.sandboxes import PVC, NetworkPolicy, NetworkRule, Volume

from cubeplex.config import config
from cubeplex.sandbox.base import (
    ExecuteResult,
    FileEntry,
    ProcessHandle,
    ProcessHandleNotFoundError,
    ProcessOutput,
    ProcessSnapshot,
    SandboxCapabilities,
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
)
from cubeplex.sandbox_policy.rules import SandboxNetworkPolicy

_OPENSANDBOX_CAPABILITIES = SandboxCapabilities(
    background=True,
    reconnect=True,
    persistent_volume=True,
    pause=True,
    reset_rootfs=False,
    terminal=True,
    browser=True,
    egress_proxy=True,
    network_policy=True,
)

_PLACEHOLDER_VALUES = frozenset(
    {"USE ENV", "REPLACE_ME", "CHANGE_ME", "CHANGEME", "TODO", "YOUR_API_KEY"}
)

_orig_aiter_sse_events = _cmd_adapter.aiter_sse_events  # type: ignore[attr-defined]


async def _fast_aiter_sse_events(response: Any) -> Any:
    """Stream SSE events and exit immediately upon execution completion.

    OpenSandbox SDK's default foreground streaming drains to EOF instead of
    breaking on ``execution_complete`` / ``error``, which adds a ~1s delay per
    call because the execd server holds the chunked HTTP connection open for
    a grace-period sleep (#1528 in opensandbox only broke for background mode).
    By terminating the event stream as soon as terminal completion events arrive,
    foreground commands complete in milliseconds over standard SSE with zero
    polling.
    """
    async for event in _orig_aiter_sse_events(response):
        yield event
        node = _decode_sse_event_data(getattr(event, "data", ""))
        if node is not None and node.type in ("execution_complete", "error"):
            break


_cmd_adapter.aiter_sse_events = _fast_aiter_sse_events  # type: ignore[attr-defined]


def _is_timeout_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "timeout" in text or "timed out" in text


def _is_not_found_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 404
    return "not found" in str(exc).lower() or "404" in str(exc)


def _is_conflict_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 409:
        return True
    return "conflict" in str(exc).lower()


@contextmanager
def _as_sandbox_error() -> Iterator[None]:
    """Translate OpenSandbox exceptions into driver-agnostic ``SandboxError``."""
    try:
        yield
    except _ProviderError as exc:
        raise SandboxError(str(exc)) from exc


def _translate_lifecycle_error(exc: Exception, sandbox_id: str) -> SandboxError:
    if isinstance(exc, SandboxError):
        return exc
    if _is_conflict_error(exc):
        return SandboxConflictError(str(exc))
    if _is_not_found_error(exc):
        return SandboxInstanceGoneError(sandbox_id)
    return SandboxError(str(exc))


def _entry_to_file_entry(entry: EntryInfo) -> FileEntry:
    name = posixpath.basename(entry.path.rstrip("/")) or entry.path
    mod = entry.modified_at
    modified_at = mod.replace(tzinfo=UTC) if mod.tzinfo is None else mod.astimezone(UTC)
    return FileEntry(
        path=entry.path,
        name=name,
        is_dir=entry.entry_type == "directory",
        size=int(entry.size or 0),
        modified_at=modified_at,
    )


def _to_opensandbox_network_policy(
    policy: SandboxNetworkPolicy | None,
) -> NetworkPolicy | None:
    if policy is None:
        return None
    return NetworkPolicy(
        defaultAction=policy.default_action,
        egress=[NetworkRule(action=r.action, target=r.target) for r in policy.egress],
    )


def _configured_text(key: str) -> str:
    raw = config.get(key)
    if raw is None:
        return ""
    text = str(raw).strip()
    if "@format" in text or "{env[" in text:
        return ""
    return text


def _validate_required_setting(key: str, env_name: str) -> str:
    value = _configured_text(key)
    if not value:
        raise RuntimeError(f"{env_name} is required when sandbox.enabled=true")
    normalized = value.lower()
    if (
        value.upper() in _PLACEHOLDER_VALUES
        or normalized.startswith("change_me")
        or normalized in {"replace_me", "use env"}
    ):
        raise RuntimeError(f"{env_name} must not use a placeholder value")
    return value


class OpenSandbox(PosixSandboxMixin):
    """Sandbox backed by a remote OpenSandbox container.

    Agent shell commands run as the configured non-root user (``run_uid`` /
    ``run_gid``, default cubeplex 1000:1000) via OpenSandbox ``RunCommandOpts``.
    Infrastructure helpers (browser stack, workspace ownership fixups) call
    ``execute(..., as_root=True)`` so supervisord can still drop privileges and
    chown PVC contents.
    """

    def __init__(
        self,
        *,
        sandbox: opensandbox.Sandbox,
        workdir: str = "/workspace",
        user_sandbox_id: str | None = None,
        run_uid: int | None = 1000,
        run_gid: int | None = 1000,
        run_user: str | None = "cubeplex",
    ) -> None:
        self._sandbox = sandbox
        self._workdir = workdir
        self._user_sandbox_id = user_sandbox_id
        self._run_env: dict[str, str] = {}
        self._run_uid = run_uid
        self._run_gid = run_gid
        self._run_user = run_user
        self._log_cursors: dict[str, int | None] = {}

    @property
    def id(self) -> str:
        return self._sandbox.id

    @property
    def workdir(self) -> str:
        return self._workdir

    @property
    def provider_name(self) -> str:
        return "opensandbox"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return _OPENSANDBOX_CAPABILITIES

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
        # Merge: run-level env (set by manager) is the base; per-call envs win.
        merged = {**(self._run_env or {}), **(envs or {})}
        uid: int | None = None if as_root else self._run_uid
        gid: int | None = None if as_root or uid is None else self._run_gid
        opts = RunCommandOpts(
            working_directory=self._workdir,
            envs=merged if merged else None,
            timeout=timedelta(seconds=timeout) if timeout is not None else None,
            uid=uid,
            gid=gid,
        )

        output_parts: list[str] = []

        async def _handle_output(msg: Any) -> None:
            text = getattr(msg, "text", "") or ""
            if not text:
                return
            if on_chunk is not None:
                try:
                    on_chunk(text)
                except Exception:
                    logger.exception("OpenSandbox on_chunk failed")
            output_parts.append(text)

        handlers = ExecutionHandlers(
            on_stdout=_handle_output,
            on_stderr=_handle_output,
            skip_accumulation=True,
        )

        async def _run() -> ExecuteResult:
            with _as_sandbox_error():
                execution = await self._sandbox.commands.run(command, opts=opts, handlers=handlers)
                output = "".join(output_parts)
                if not output and getattr(execution, "logs", None):
                    lines = [m.text for m in execution.logs.stdout] + [
                        m.text for m in execution.logs.stderr
                    ]
                    output = "\n".join(lines) if lines else ""

                exit_code = getattr(execution, "exit_code", None)
                exec_id = getattr(execution, "id", None)
                if not isinstance(exit_code, int) and isinstance(exec_id, str):
                    try:
                        status = await self._sandbox.commands.get_command_status(exec_id)
                        code = getattr(status, "exit_code", None)
                        if isinstance(code, int):
                            exit_code = code
                    except Exception as e:
                        logger.warning("Could not get exit code for command: {}", e)
                if exit_code is not None and not isinstance(exit_code, int):
                    exit_code = 0

                return ExecuteResult(output=output, exit_code=exit_code)

        try:
            if timeout is not None:
                return await asyncio.wait_for(_run(), timeout=timeout)
            return await _run()
        except TimeoutError:
            return ExecuteResult(output="[timeout]", exit_code=-1)
        except SandboxError as exc:
            if timeout is not None and _is_timeout_error(exc):
                return ExecuteResult(output="[timeout]", exit_code=-1)
            raise

    def supports_background(self) -> bool:
        return True

    def supports_background_reconnect(self) -> bool:
        return True

    async def _raise_if_instance_gone(self) -> None:
        try:
            info = await self._sandbox.get_info()
        except _ProviderApiError as exc:
            if exc.status_code == 404:
                raise SandboxInstanceGoneError(self.id) from exc
            return
        except Exception:
            return
        if info.status.state in ("Failed", "Terminated", "Succeed"):
            raise SandboxInstanceGoneError(self.id)

    async def observe(self, handle: ProcessHandle) -> ProcessSnapshot:
        with _as_sandbox_error():
            try:
                status = await self._sandbox.commands.get_command_status(handle.provider_ref)
            except _ProviderApiError as exc:
                if exc.status_code == 404:
                    raise ProcessHandleNotFoundError(handle.provider_ref) from exc
                raise
        running = getattr(status, "running", None)
        code = getattr(status, "exit_code", None)
        raw_error = getattr(status, "error", None)
        error = raw_error if isinstance(raw_error, str) else None
        if not isinstance(running, bool) or (code is not None and type(code) is not int):
            raise SandboxError("provider returned an invalid process status")
        return ProcessSnapshot(
            status="running" if running else "exited", exit_code=code, error=error
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
        on_started: Callable[[str], Any] | None = None,
    ) -> ProcessHandle:
        del on_chunk
        merged = {**self._run_env, **(envs or {})}
        uid: int | None = None if as_root else self._run_uid
        gid: int | None = None if as_root or uid is None else self._run_gid
        opts = RunCommandOpts(
            working_directory=self._workdir,
            envs=merged if merged else None,
            # Subagent deadlines survive a worker restart; main commands retain their lifecycle.
            timeout=(
                timedelta(seconds=timeout) if provider_timeout and timeout is not None else None
            ),
            uid=uid,
            gid=gid,
            background=True,
        )
        started: list[str] = []

        async def _on_init(msg: Any) -> None:
            eid = getattr(msg, "execution_id", None) or getattr(msg, "id", None)
            if eid is None:
                return
            ref = str(eid)
            started.append(ref)
            if on_started is not None:
                maybe = on_started(ref)
                if inspect.isawaitable(maybe):
                    await maybe

        try:
            with _as_sandbox_error():
                execution = await self._sandbox.commands.run(
                    command,
                    opts=opts,
                    handlers=ExecutionHandlers(on_init=_on_init, skip_accumulation=True),
                )
        except SandboxError:
            await self._raise_if_instance_gone()
            raise
        ref = started[0] if started else str(execution.id or "")
        self._log_cursors[ref] = 0
        if on_started is not None and not started and ref:
            maybe = on_started(ref)
            if inspect.isawaitable(maybe):
                await maybe
        return ProcessHandle(command_id="", provider_ref=ref)

    async def poll(self, handle: ProcessHandle) -> ProcessSnapshot:
        try:
            status = await self.observe(handle)
            output = await self.read_output(handle)
        except SandboxError:
            await self._raise_if_instance_gone()
            raise
        return ProcessSnapshot(
            status=status.status,
            exit_code=status.exit_code,
            new_output=output.new_output,
            log_cursor=output.log_cursor,
            error=status.error,
        )

    async def read_output(self, handle: ProcessHandle) -> ProcessOutput:
        ref = handle.provider_ref
        cursor = (
            int(handle.log_cursor) if handle.log_cursor is not None else self._log_cursors.get(ref)
        )
        with _as_sandbox_error():
            logs = await self._sandbox.commands.get_background_command_logs(ref, cursor=cursor)
        new_output = getattr(logs, "content", "") or ""
        next_cursor = getattr(logs, "cursor", None)
        serialized_cursor = str(next_cursor) if next_cursor is not None else handle.log_cursor
        return ProcessOutput(
            new_output=new_output,
            log_cursor=serialized_cursor,
        )

    async def kill(self, handle: ProcessHandle) -> None:
        with _as_sandbox_error():
            await self._sandbox.commands.interrupt(handle.provider_ref)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        """Write files then chown by numeric uid so agent can edit them.

        The files API typically writes as root; without a follow-up chown the
        agent (uid 1000) cannot edit the uploaded path.
        """
        if not files:
            return
        with _as_sandbox_error():
            for path, content in files:
                await self._sandbox.files.write_file(path, content)
        await self._chown_paths([path for path, _ in files])

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        with _as_sandbox_error():
            result = []
            for path in paths:
                try:
                    content = await self._sandbox.files.read_bytes(path)
                except Exception as exc:
                    if "404" in str(exc):
                        raise FileNotFoundError(path) from exc
                    raise
                result.append((path, content))
            return result

    async def _chown_paths(self, paths: list[str]) -> None:
        """Best-effort chown of paths to run_uid:run_gid (root only)."""
        if self._run_uid is None or not paths:
            return
        gid = self._run_gid if self._run_gid is not None else self._run_uid
        quoted = " ".join(shlex.quote(p) for p in paths)
        try:
            result = await self.execute(
                f"chown {self._run_uid}:{gid} {quoted} 2>/dev/null || true",
                timeout=60,
                as_root=True,
            )
        except Exception as exc:
            logger.warning("chown after upload failed on sandbox {}: {}", self.id, exc)
            return
        if result.exit_code not in (0, None):
            logger.warning(
                "chown after upload failed on sandbox {}: {}",
                self.id,
                result.output,
            )

    async def list_dir(self, path: str) -> list[FileEntry]:
        try:
            entries = await self._sandbox.files.list_directory(
                DirectoryListEntry(path=path, depth=1)
            )
        except FileNotFoundError:
            raise
        except (_ProviderError, _ProviderApiError, TimeoutError, OSError) as exc:
            if _is_not_found_error(exc):
                raise FileNotFoundError(path) from exc
            raise SandboxError(str(exc)) from exc
        return [_entry_to_file_entry(entry) for entry in entries]

    async def stat_file(self, path: str) -> FileEntry:
        try:
            info_map = await self._sandbox.files.get_file_info([path])
        except FileNotFoundError:
            raise
        except (_ProviderError, _ProviderApiError, TimeoutError, OSError) as exc:
            if _is_not_found_error(exc):
                raise FileNotFoundError(path) from exc
            raise SandboxError(str(exc)) from exc
        entry = info_map.get(path)
        if entry is None:
            raise FileNotFoundError(path)
        return _entry_to_file_entry(entry)

    async def read_text(self, path: str, *, max_bytes: int | None = None) -> str:
        try:
            if max_bytes is not None:
                info_map = await self._sandbox.files.get_file_info([path])
                entry = info_map.get(path)
                if entry is not None and entry.size > max_bytes:
                    raise SandboxFileTooLargeError(
                        f"{path} exceeds max_bytes={max_bytes} ({entry.size} bytes)"
                    )
            text = await self._sandbox.files.read_file(path)
        except FileNotFoundError:
            raise
        except (_ProviderError, _ProviderApiError, TimeoutError, OSError) as exc:
            if _is_not_found_error(exc):
                raise FileNotFoundError(path) from exc
            raise SandboxError(str(exc)) from exc
        encoded_size = len(text.encode("utf-8"))
        if max_bytes is not None and encoded_size > max_bytes:
            raise SandboxFileTooLargeError(
                f"{path} exceeds max_bytes={max_bytes} ({encoded_size} bytes)"
            )
        return text

    @asynccontextmanager
    async def stream_file(self, path: str) -> AsyncIterator[AsyncIterator[bytes]]:
        try:
            raw_or_coro: AsyncIterator[bytes] | Awaitable[AsyncIterator[bytes]] = (
                self._sandbox.files.read_bytes_stream(path)
            )
            raw_stream: AsyncIterator[bytes] = (
                await raw_or_coro if inspect.isawaitable(raw_or_coro) else raw_or_coro
            )
        except FileNotFoundError:
            raise
        except (_ProviderError, _ProviderApiError, TimeoutError, OSError) as exc:
            if _is_not_found_error(exc):
                raise FileNotFoundError(path) from exc
            raise SandboxError(str(exc)) from exc

        async def _iter_chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in raw_stream:
                    yield chunk
            except (_ProviderError, _ProviderApiError, TimeoutError, OSError) as exc:
                raise SandboxError(str(exc)) from exc

        try:
            yield _iter_chunks()
        finally:
            aclose = getattr(raw_stream, "aclose", None)
            if callable(aclose):
                with suppress(Exception):
                    await aclose()

    async def close(self) -> None:
        await self._sandbox.close()

    async def is_healthy(self) -> bool:
        try:
            return bool(await self._sandbox.is_healthy())
        except Exception:
            return False

    async def pause(self) -> None:
        try:
            await self._sandbox.pause()
        except Exception as exc:
            raise _translate_lifecycle_error(exc, self.id) from exc

    async def renew(self, ttl_seconds: int) -> None:
        try:
            await self._sandbox.renew(timedelta(seconds=ttl_seconds))
        except Exception as exc:
            raise _translate_lifecycle_error(exc, self.id) from exc

    @classmethod
    async def connect_or_resume(  # type: ignore[override]
        cls,
        sandbox_id: str,
        *,
        conn_config: ConnectionConfig | None = None,
        resume_timeout: int = 30,
        workdir: str = "/workspace",
        run_uid: int | None = 1000,
        run_gid: int | None = 1000,
        run_user: str | None = "cubeplex",
        **_: object,
    ) -> OpenSandbox:
        try:
            raw = await opensandbox.Sandbox.resume(
                sandbox_id,
                connection_config=conn_config,
                resume_timeout=timedelta(seconds=resume_timeout),
            )
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc
        return cls(
            sandbox=raw,
            workdir=workdir,
            run_uid=run_uid,
            run_gid=run_gid,
            run_user=run_user,
        )


class OpenSandboxProvider(SandboxProvider):
    """Control-plane ``SandboxProvider`` backed by OpenSandbox server."""

    @property
    def name(self) -> str:
        return "opensandbox"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return _OPENSANDBOX_CAPABILITIES

    @property
    def default_workdir(self) -> str:
        return _configured_text("sandbox.workdir") or "/workspace"

    @property
    def default_image(self) -> str | None:
        return _configured_text("sandbox.image") or None

    def validate_config(self) -> None:
        _validate_required_setting("sandbox.domain", "CUBEPLEX_SANDBOX__DOMAIN")
        _validate_required_setting("sandbox.image", "CUBEPLEX_SANDBOX__IMAGE")
        _validate_required_setting("sandbox.api_key", "CUBEPLEX_SANDBOX__API_KEY")

    def _default_run_identity(self) -> RunIdentity:
        raw_user = config.get("sandbox.run_user", "cubeplex")
        raw_uid = config.get("sandbox.run_uid", 1000)
        raw_gid = config.get("sandbox.run_gid", 1000)
        return RunIdentity(
            user=str(raw_user) if raw_user else None,
            uid=int(raw_uid) if raw_uid is not None else None,
            gid=int(raw_gid) if raw_gid is not None else None,
        )

    def _request_timeout_seconds(self) -> float:
        return float(config.get("sandbox.request_timeout", 1860))

    def _build_connection_config(
        self,
        *,
        request_timeout_seconds: float | None = None,
    ) -> ConnectionConfig:
        timeout_sec = (
            float(request_timeout_seconds)
            if request_timeout_seconds is not None
            else self._request_timeout_seconds()
        )
        return ConnectionConfig(
            domain=_configured_text("sandbox.domain"),
            api_key=_configured_text("sandbox.api_key"),
            request_timeout=timedelta(seconds=timeout_sec),
            use_server_proxy=bool(config.get("sandbox.use_server_proxy", True)),
        )

    @staticmethod
    def _build_volumes(spec: SandboxCreateSpec) -> list[Volume] | None:
        if not spec.volume_key:
            return None
        return [
            Volume(
                name="user-workspace",
                pvc=PVC(
                    claimName=spec.volume_key,
                    createIfNotExists=True,
                    deleteOnSandboxTermination=False,
                    storage=spec.storage,
                ),
                mountPath=spec.volume_mount_path,
                readOnly=False,
            )
        ]

    async def create(
        self,
        spec: SandboxCreateSpec,
        *,
        on_created: Callable[[str], Awaitable[None]] | None = None,
    ) -> OpenSandbox:
        create_conn = self._build_connection_config(
            request_timeout_seconds=float(spec.create_timeout_seconds)
        )
        create_kwargs: dict[str, Any] = {
            "timeout": timedelta(seconds=spec.ttl_seconds),
            "ready_timeout": timedelta(seconds=spec.ready_timeout_seconds),
            "resource": {"cpu": spec.resource_cpu, "memory": spec.resource_memory},
            "secure_access": bool(config.get("sandbox.secure_access", False)),
            "connection_config": create_conn,
            "skip_health_check": True,
        }
        if spec.env:
            create_kwargs["env"] = spec.env
        if spec.metadata:
            create_kwargs["metadata"] = spec.metadata
        net_policy = _to_opensandbox_network_policy(spec.network_policy)
        if net_policy is not None:
            create_kwargs["network_policy"] = net_policy
        volumes = self._build_volumes(spec)
        if volumes is not None:
            create_kwargs["volumes"] = volumes

        with _as_sandbox_error():
            raw = await opensandbox.Sandbox.create(spec.image, **create_kwargs)
        try:
            if on_created is not None:
                await on_created(raw.id)
            with _as_sandbox_error():
                await raw.check_ready(
                    timedelta(seconds=spec.ready_timeout_seconds),
                    timedelta(milliseconds=200),
                )
        finally:
            close_fn = getattr(raw, "close", None)
            if callable(close_fn):
                with suppress(Exception):
                    await close_fn()

        std_conn = self._build_connection_config(
            request_timeout_seconds=float(spec.request_timeout_seconds)
        )
        try:
            rebound = await opensandbox.Sandbox.connect(
                raw.id,
                connection_config=std_conn,
                skip_health_check=True,
            )
        except Exception as exc:
            if isinstance(exc, SandboxError):
                raise
            raise SandboxError(
                f"reconnect failed after sandbox creation ({raw.id}): {exc}"
            ) from exc

        return OpenSandbox(
            sandbox=rebound,
            workdir=spec.workdir,
            user_sandbox_id=spec.user_sandbox_id,
            run_uid=spec.run_identity.uid,
            run_gid=spec.run_identity.gid,
            run_user=spec.run_identity.user,
        )

    async def connect(
        self,
        sandbox_id: str,
        *,
        workdir: str | None = None,
        user_sandbox_id: str | None = None,
        run_identity: RunIdentity | None = None,
    ) -> OpenSandbox:
        ident = run_identity or self._default_run_identity()
        std_conn = self._build_connection_config()
        try:
            raw = await opensandbox.Sandbox.connect(
                sandbox_id,
                connection_config=std_conn,
                skip_health_check=True,
            )
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc
        return OpenSandbox(
            sandbox=raw,
            workdir=workdir or self.default_workdir,
            user_sandbox_id=user_sandbox_id,
            run_uid=ident.uid,
            run_gid=ident.gid,
            run_user=ident.user,
        )

    async def connect_observer(
        self,
        sandbox_id: str,
        *,
        timeout_seconds: int = 30,
    ) -> OpenSandbox:
        ident = self._default_run_identity()
        conn = self._build_connection_config(request_timeout_seconds=float(timeout_seconds))
        try:
            raw = await opensandbox.Sandbox.connect(
                sandbox_id,
                connection_config=conn,
                skip_health_check=True,
            )
        except SandboxError:
            raise
        except Exception as exc:
            # Data-plane endpoint 404s do not prove the container itself is gone.
            raise SandboxError(f"could not observe sandbox instance {sandbox_id}: {exc}") from exc
        return OpenSandbox(
            sandbox=raw,
            workdir=self.default_workdir,
            run_uid=ident.uid,
            run_gid=ident.gid,
            run_user=ident.user,
        )

    async def resume(
        self,
        sandbox_id: str,
        *,
        workdir: str | None = None,
        user_sandbox_id: str | None = None,
        run_identity: RunIdentity | None = None,
    ) -> OpenSandbox:
        ident = run_identity or self._default_run_identity()
        std_conn = self._build_connection_config()
        resume_timeout = float(config.get("sandbox.resume_timeout", 30))
        try:
            raw = await opensandbox.Sandbox.resume(
                sandbox_id,
                connection_config=std_conn,
                resume_timeout=timedelta(seconds=resume_timeout),
            )
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc
        return OpenSandbox(
            sandbox=raw,
            workdir=workdir or self.default_workdir,
            user_sandbox_id=user_sandbox_id,
            run_uid=ident.uid,
            run_gid=ident.gid,
            run_user=ident.user,
        )

    @asynccontextmanager
    async def _manager_client(
        self, *, timeout_seconds: float
    ) -> AsyncIterator[opensandbox.SandboxManager]:
        mgr = await opensandbox.SandboxManager.create(
            connection_config=self._build_connection_config(request_timeout_seconds=timeout_seconds)
        )
        try:
            yield mgr
        finally:
            close_fn = getattr(mgr, "close", None)
            if callable(close_fn):
                with suppress(Exception):
                    await close_fn()

    async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
        try:
            async with self._manager_client(timeout_seconds=15.0) as mgr:
                info = await mgr.get_sandbox_info(sandbox_id)
        except Exception as exc:
            if _is_not_found_error(exc):
                return None
            raise _translate_lifecycle_error(exc, sandbox_id) from exc
        state = str(getattr(getattr(info, "status", None), "state", "") or "")
        if state == "Paused":
            return ProviderInstanceState.PAUSED
        if state == "Running":
            return ProviderInstanceState.RUNNING
        if state == "Failed":
            return ProviderInstanceState.FAILED
        if state in ("Terminated", "Succeed"):
            return ProviderInstanceState.TERMINATED
        return ProviderInstanceState.TRANSITIONAL

    async def pause(self, sandbox_id: str) -> None:
        try:
            async with self._manager_client(timeout_seconds=30.0) as mgr:
                await mgr.pause_sandbox(sandbox_id)
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc

    async def terminate(self, sandbox_id: str) -> None:
        try:
            async with self._manager_client(timeout_seconds=30.0) as mgr:
                await mgr.kill_sandbox(sandbox_id)
        except Exception as exc:
            if _is_not_found_error(exc):
                return
            raise _translate_lifecycle_error(exc, sandbox_id) from exc

    async def renew_ttl(self, sandbox_id: str, ttl_seconds: int) -> None:
        try:
            async with self._manager_client(timeout_seconds=15.0) as mgr:
                await mgr.renew_sandbox(sandbox_id, timedelta(seconds=ttl_seconds))
        except Exception as exc:
            raise _translate_lifecycle_error(exc, sandbox_id) from exc

    def build_panel_upstream(
        self,
        *,
        sandbox_id: str,
        port: int,
        path: str,
        query: str,
    ) -> PanelUpstream:
        domain = _configured_text("sandbox.domain")
        if not domain:
            raise SandboxError("OpenSandbox panel proxy requires sandbox.domain")
        http_scheme = "https" if domain.startswith("https://") else "http"
        ws_scheme = "wss" if http_scheme == "https" else "ws"
        host = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
        clean_path = path.lstrip("/")
        qs = f"?{query}" if query else ""
        suffix = f"/sandboxes/{sandbox_id}/proxy/{port}/{clean_path}{qs}"
        return PanelUpstream(
            http_url=f"{http_scheme}://{host}{suffix}",
            http_headers={"accept-encoding": "identity"},
            ws_url=f"{ws_scheme}://{host}{suffix}",
            ws_headers={},
        )
