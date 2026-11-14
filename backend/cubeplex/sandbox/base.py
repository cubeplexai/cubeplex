"""Sandbox base class — async-first interface for code execution environments."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal, TypeVar

from cubeplex.skills.sync_tar import SKILLS_DELTA_TGZ_PATH, build_extract_and_remove_cmd

if TYPE_CHECKING:
    from cubeplex.parsers import FileReadOutput, ParseOptions

T = TypeVar("T")


class SandboxError(Exception):
    """Driver-agnostic sandbox failure.

    Each backend driver translates its own provider-specific exceptions into
    this type so callers above the driver layer (API routes, services) can
    react to sandbox failures without importing or depending on a particular
    driver (OpenSandbox, ...).
    """


class SandboxConflictError(SandboxError):
    """Sandbox is in a state that conflicts with the requested operation
    (e.g. restart while provisioning)."""


class SandboxInstanceGoneError(SandboxError):
    """The provider confirms this exact instance is absent or terminal."""

    def __init__(self, sandbox_instance_id: str) -> None:
        self.sandbox_instance_id = sandbox_instance_id
        super().__init__(f"original sandbox instance {sandbox_instance_id} no longer exists")


class ProcessHandleNotFoundError(SandboxError):
    """The provider cannot find a process previously returned by command start."""

    def __init__(self, provider_ref: str) -> None:
        super().__init__(f"provider process record {provider_ref} is unavailable")


class SandboxCapabilityUnsupportedError(SandboxError):
    """Requested operation is not supported by this sandbox provider."""

    def __init__(self, capability: str, provider: str) -> None:
        self.capability = capability
        self.provider = provider
        super().__init__(f"provider {provider!r} does not support capability {capability!r}")


class SandboxFileTooLargeError(SandboxError):
    """File exceeds caller-supplied max_bytes limit."""


@dataclass(frozen=True, slots=True)
class SandboxCapabilities:
    """Capabilities advertised by a sandbox provider or handle."""

    background: bool = False
    reconnect: bool = False
    persistent_volume: bool = False
    pause: bool = False
    reset_rootfs: bool = False
    terminal: bool = False
    browser: bool = False
    egress_proxy: bool = False
    network_policy: bool = False


@dataclass(frozen=True, slots=True)
class FileEntry:
    """Metadata for a file or directory entry inside a sandbox."""

    path: str
    name: str
    is_dir: bool
    size: int
    modified_at: datetime


@dataclass
class ExecuteResult:
    """Result of a shell command execution."""

    output: str
    exit_code: int | None = None


@dataclass
class ProcessHandle:
    """CubePlex-owned command id plus a driver-private provider handle."""

    command_id: str
    provider_ref: str
    log_cursor: str | None = None
    deadline_at: datetime | None = None


@dataclass
class ProcessSnapshot:
    """Incremental status from ``Sandbox.poll``."""

    status: Literal["running", "exited", "killed"]
    exit_code: int | None = None
    new_output: str = ""
    log_cursor: str | None = None
    error: str | None = None


@dataclass
class ProcessOutput:
    """Unacknowledged output returned from a provider cursor."""

    new_output: str = ""
    log_cursor: str | None = None


@dataclass
class BrowserEndpoint:
    """Reachable endpoint for the sandbox's Neko browser live view.

    ``url`` must be embeddable directly in a browser ``<iframe>``. If ``headers``
    is non-empty the URL is *not* directly embeddable — a browser cannot attach
    request headers to an iframe navigation — and a same-origin proxy that
    injects them is required before handing a URL to the frontend.
    """

    url: str
    headers: dict[str, str] = field(default_factory=dict)


class Sandbox(ABC):
    """Async-first sandbox base class.

    Agent-facing: only `execute` is registered as a tool.
    Infrastructure-facing: `upload`/`download` for binary file transfer
    (used by API endpoints, SandboxManager, skills sync — NOT agent tools).
    """

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique identifier for this sandbox instance."""
        ...

    @property
    @abstractmethod
    def workdir(self) -> str:
        """Working directory for command execution."""
        ...

    @property
    def provider_name(self) -> str:
        """Identifier of the provider backing this sandbox instance."""
        return "unknown"

    @property
    def capabilities(self) -> SandboxCapabilities:
        """Capabilities advertised by this sandbox instance."""
        return SandboxCapabilities()

    @property
    def user_sandbox_id(self) -> str | None:
        """CubePlex ``user_sandboxes.id`` once the instance is attached."""
        return None

    @abstractmethod
    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk: Callable[[str], None] | None = None,
    ) -> ExecuteResult:
        """Execute a shell command. Returns combined stdout+stderr and exit code.

        Args:
            command: Shell command to run.
            timeout: Optional execution timeout in seconds.
            envs: Per-call env overrides injected into the command process.
                  Merged with any run-level env set via ``set_run_env``; per-call
                  values win on conflict.
            as_root: When True, run as the container control-plane user (root)
                  instead of the sandbox agent user. Used for infra helpers
                  (browser stack, workspace chown). Ignored by drivers that
                  have no privilege separation.
            on_chunk: Optional callback for stdout/stderr text as it arrives.
                  Exceptions in the callback must not fail the command.
        """
        ...

    def set_run_env(self, env: dict[str, str]) -> None:
        """Attach a persistent env dict injected into every subsequent execute call.

        Called by SandboxManager after sandbox creation or reuse to load fresh
        egress placeholders.  The default no-op is correct for backends that do
        not support per-command env injection (e.g. LocalSandbox, LazySandbox
        before the underlying backend is resolved).  Concrete backends that DO
        support it (OpenSandbox) override this.
        """
        return  # no-op default; OpenSandbox overrides

    def supports_background(self) -> bool:
        """Whether ``start`` / ``poll`` / ``kill`` are implemented."""
        return self.capabilities.background

    async def run_with_backend(self, operation: Callable[[Sandbox], Awaitable[T]]) -> T:
        """Run an operation against a concrete backend for its entire lifetime."""
        return await operation(self)

    def supports_background_reconnect(self) -> bool:
        """Whether another worker can recover a persisted process handle."""
        return self.capabilities.reconnect

    async def observe(self, handle: ProcessHandle) -> ProcessSnapshot:
        """Observe execution independently of fetching or acknowledging output."""
        del handle
        raise SandboxError("this sandbox driver does not support process observation")

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
        del command, timeout, provider_timeout, envs, as_root, on_chunk, on_started
        raise SandboxError("this sandbox driver does not support background commands")

    async def poll(self, handle: ProcessHandle) -> ProcessSnapshot:
        del handle
        raise SandboxError("this sandbox driver does not support background commands")

    async def read_output(self, handle: ProcessHandle) -> ProcessOutput:
        """Read output after ``handle.log_cursor`` without acknowledging it."""

        snapshot = await self.poll(handle)
        return ProcessOutput(
            new_output=snapshot.new_output,
            log_cursor=snapshot.log_cursor,
        )

    async def acknowledge_output(self, handle: ProcessHandle, cursor: str) -> None:
        """Advance driver-local output state after the durable cursor commits."""

        handle.log_cursor = cursor

    async def kill(self, handle: ProcessHandle) -> None:
        del handle
        raise SandboxError("this sandbox driver does not support background commands")

    @abstractmethod
    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        """Upload files into the sandbox. Each tuple is (absolute_path, content)."""
        ...

    @abstractmethod
    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        """Download files from the sandbox. Returns list of (path, content) tuples."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release sandbox resources."""
        ...

    async def is_healthy(self) -> bool:
        """Check whether the sandbox handle is healthy and responsive."""
        return True

    def supports_pause(self) -> bool:
        """Whether this driver can natively pause/resume. Default False so the
        manager picks kill-on-idle for non-capable drivers."""
        return self.capabilities.pause

    async def pause(self) -> None:
        """Suspend this sandbox, preserving state. Override in capable drivers."""
        raise NotImplementedError("pause is not supported by this sandbox backend")

    async def renew(self, ttl_seconds: int) -> None:
        """Extend the provider-side expiration. No-op for backends without TTL."""
        del ttl_seconds
        return  # no-op default; backends with TTL override

    @classmethod
    async def connect_or_resume(cls, sandbox_id: str, **kwargs: object) -> Sandbox:
        """Connect to a sandbox, resuming it from `paused` if necessary, and
        return a fresh handle with re-resolved endpoints. OpenSandbox calls
        `Sandbox.resume(...)` server-side then connects; e2b's `connect`
        auto-resumes. Override in capable drivers."""
        raise NotImplementedError("connect_or_resume is not supported by this sandbox backend")

    async def list_dir(self, path: str) -> list[FileEntry]:
        """List direct children (*depth=1*) at *path*."""
        del path
        raise NotImplementedError("list_dir is not supported by this sandbox backend")

    async def stat_file(self, path: str) -> FileEntry:
        """Return metadata for *path*, raising ``FileNotFoundError`` if absent."""
        del path
        raise NotImplementedError("stat_file is not supported by this sandbox backend")

    async def read_text(self, path: str, *, max_bytes: int | None = None) -> str:
        """Read UTF-8 text from *path*, respecting optional *max_bytes*."""
        del path, max_bytes
        raise NotImplementedError("read_text is not supported by this sandbox backend")

    def stream_file(self, path: str) -> AbstractAsyncContextManager[AsyncIterator[bytes]]:
        """Yield raw byte chunks from *path*, raising ``FileNotFoundError`` before yield."""
        del path
        raise NotImplementedError("stream_file is not supported by this sandbox backend")

    async def sync_archive(
        self,
        tarball: bytes | None,
        *,
        dest: str,
        repush_names: Sequence[str] = (),
        remove_paths: Sequence[str] = (),
    ) -> None:
        """Upload *tarball* (when non-empty) and extract/prune entries inside *dest*."""
        has_push = bool(tarball)
        if tarball:
            await self.upload([(SKILLS_DELTA_TGZ_PATH, tarball)])
        cmd = build_extract_and_remove_cmd(
            skills_root=dest,
            has_push=has_push,
            to_repush_names=list(repush_names),
            to_remove=list(remove_paths),
        )
        if cmd:
            await self.execute(cmd)

    async def ensure_workspace_owner(self) -> None:
        """Ensure the sandbox workdir exists and has appropriate ownership."""
        return

    # Container port the Neko browser live view is served on.
    BROWSER_PORT = 8080

    # Container port the ttyd web terminal is served on.
    TERMINAL_PORT = 7681

    async def start_browser(self) -> None:
        """Start the on-demand Neko browser stack inside the sandbox."""
        raise SandboxCapabilityUnsupportedError("browser", self.provider_name)

    async def start_terminal(self) -> None:
        """Start the on-demand ttyd terminal inside the sandbox."""
        raise SandboxCapabilityUnsupportedError("terminal", self.provider_name)

    async def get_terminal_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        """Return a reachable endpoint for the ttyd terminal."""
        del expires_in
        raise SandboxCapabilityUnsupportedError("terminal", self.provider_name)

    async def get_browser_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        """Return a reachable endpoint for the Neko browser live view."""
        del expires_in
        raise SandboxCapabilityUnsupportedError("browser", self.provider_name)

    async def file_read(
        self,
        path: str,
        *,
        options: ParseOptions | None = None,
        conversation_id: str | None = None,
    ) -> FileReadOutput:
        """Read and parse a file at ``path`` inside the sandbox.

        Default impl: download bytes via ``self.download`` + dispatch through the
        parser registry. Subclasses may override if they can do parsing natively.
        """
        from cubeplex.parsers import ParseOptions as _ParseOptions
        from cubeplex.parsers import get_parser_registry

        return await get_parser_registry().dispatch(
            sandbox=self,
            path=path,
            options=options or _ParseOptions(),
            conversation_id=conversation_id,
        )
