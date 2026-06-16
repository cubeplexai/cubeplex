"""Sandbox base class — async-first interface for code execution environments."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cubebox.parsers import FileReadOutput, ParseOptions


class SandboxError(Exception):
    """Driver-agnostic sandbox failure.

    Each backend driver translates its own provider-specific exceptions into
    this type so callers above the driver layer (API routes, services) can
    react to sandbox failures without importing or depending on a particular
    driver (OpenSandbox, ...).
    """


@dataclass
class ExecuteResult:
    """Result of a shell command execution."""

    output: str
    exit_code: int | None = None


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

    @abstractmethod
    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
    ) -> ExecuteResult:
        """Execute a shell command. Returns combined stdout+stderr and exit code.

        Args:
            command: Shell command to run.
            timeout: Optional execution timeout in seconds.
            envs: Per-call env overrides injected into the command process.
                  Merged with any run-level env set via ``set_run_env``; per-call
                  values win on conflict.
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

    def supports_pause(self) -> bool:
        """Whether this driver can natively pause/resume. Default False so the
        manager picks kill-on-idle for non-capable drivers."""
        return False

    async def pause(self) -> None:
        """Suspend this sandbox, preserving state. Override in capable drivers."""
        raise NotImplementedError("pause is not supported by this sandbox backend")

    @classmethod
    async def connect_or_resume(cls, sandbox_id: str, **kwargs: object) -> Sandbox:
        """Connect to a sandbox, resuming it from `paused` if necessary, and
        return a fresh handle with re-resolved endpoints. OpenSandbox calls
        `Sandbox.resume(...)` server-side then connects; e2b's `connect`
        auto-resumes. Override in capable drivers."""
        raise NotImplementedError("connect_or_resume is not supported by this sandbox backend")

    # Container port the Neko browser live view is served on.
    BROWSER_PORT = 8080

    # Container port the ttyd web terminal is served on.
    TERMINAL_PORT = 7681

    async def start_browser(self) -> None:
        """Start the on-demand Neko browser stack inside the sandbox (idempotent).

        Baked into the sandbox image at ``/usr/local/bin/start-browser.sh``; safe
        to call repeatedly (no-op if already running).
        """
        result = await self.execute("/usr/local/bin/start-browser.sh", timeout=120)
        if result.exit_code not in (0, None):
            raise RuntimeError(f"failed to start sandbox browser: {result.output}")

    async def start_terminal(self) -> None:
        """Start the on-demand ttyd terminal inside the sandbox (idempotent)."""
        result = await self.execute(
            "start-stop-daemon --start --oknodo --background"
            " --make-pidfile --pidfile /tmp/ttyd.pid"
            " --exec /usr/bin/ttyd -- -p 7681 -W -w /workspace bash"
            " && sleep 1",
            timeout=30,
        )
        if result.exit_code not in (0, None):
            raise SandboxError(f"failed to start sandbox terminal: {result.output}")

    async def get_terminal_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        """Return a reachable endpoint for the ttyd terminal.

        Backends that can expose an in-sandbox port override this; the
        default signals the capability is unavailable.
        """
        raise NotImplementedError("terminal is not supported by this sandbox backend")

    async def get_browser_endpoint(self, *, expires_in: int = 3600) -> BrowserEndpoint:
        """Return a reachable endpoint for the Neko browser live view.

        Backends that can expose an in-sandbox port override this; the default
        signals the capability is unavailable.
        """
        raise NotImplementedError("browser live view is not supported by this sandbox backend")

    def has_synced(self, skill_version_id: str) -> bool:
        """Whether ``skill_version_id`` has already been uploaded to this sandbox.

        Concrete subclasses get this for free; ``mark_synced`` is the matching
        setter. Storage is in-memory on the sandbox instance — fine because
        sandboxes are per-user and the sync set lives only as long as the
        sandbox does. Recreating a sandbox naturally re-syncs.
        """
        if not hasattr(self, "_synced_skill_version_ids"):
            self._synced_skill_version_ids: set[str] = set()
        return skill_version_id in self._synced_skill_version_ids

    def mark_synced(self, skill_version_id: str) -> None:
        if not hasattr(self, "_synced_skill_version_ids"):
            self._synced_skill_version_ids = set()
        self._synced_skill_version_ids.add(skill_version_id)

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
        from cubebox.parsers import ParseOptions as _ParseOptions
        from cubebox.parsers import get_parser_registry

        return await get_parser_registry().dispatch(
            sandbox=self,
            path=path,
            options=options or _ParseOptions(),
            conversation_id=conversation_id,
        )
