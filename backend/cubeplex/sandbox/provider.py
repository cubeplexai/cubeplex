"""Control-plane SandboxProvider contract and normalized provider types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from cubeplex.sandbox.base import (
    Sandbox,
    SandboxCapabilities,
    SandboxCapabilityUnsupportedError,
)
from cubeplex.sandbox_policy.rules import SandboxNetworkPolicy


class ProviderInstanceState(StrEnum):
    """Normalized provider-side instance lifecycle states."""

    RUNNING = "running"
    PAUSED = "paused"
    TRANSITIONAL = "transitional"
    FAILED = "failed"
    TERMINATED = "terminated"


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """Guest execution identity inside POSIX sandbox environments."""

    user: str | None = "cubeplex"
    uid: int | None = 1000
    gid: int | None = 1000


@dataclass(frozen=True, slots=True)
class SandboxCreateSpec:
    """Provider-neutral request to provision a new sandbox instance."""

    volume_key: str | None
    volume_mount_path: str
    storage: str | None
    image: str
    workdir: str
    ttl_seconds: int
    create_timeout_seconds: int
    ready_timeout_seconds: int
    request_timeout_seconds: int
    run_identity: RunIdentity
    env: dict[str, str]
    resource_cpu: str
    resource_memory: str
    network_policy: SandboxNetworkPolicy | None
    metadata: dict[str, str] = field(default_factory=dict)
    user_sandbox_id: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PanelUpstream:
    """Resolved upstream HTTP/WebSocket targets for the panel reverse proxy."""

    http_url: str
    http_headers: dict[str, str]
    ws_url: str
    ws_headers: dict[str, str]
    ws_connect_host: str | None = None
    ws_connect_port: int | None = None
    ws_server_hostname: str | None = None


class SandboxProvider(ABC):
    """Control-plane interface for provisioning, inspecting, and managing sandboxes."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier stored on ``user_sandboxes.provider``."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> SandboxCapabilities:
        """Static capability set advertised by instances of this provider."""
        ...

    @property
    @abstractmethod
    def default_workdir(self) -> str:
        """Default workspace directory path inside newly provisioned instances."""
        ...

    @property
    @abstractmethod
    def default_image(self) -> str | None:
        """Default runtime image/template name, or ``None`` when unconfigured."""
        ...

    def validate_config(self) -> None:
        """Validate deployment-time settings required when this provider is default."""
        return

    @abstractmethod
    async def create(
        self,
        spec: SandboxCreateSpec,
        *,
        on_created: Callable[[str], Awaitable[None]] | None = None,
    ) -> Sandbox:
        """Provision a new instance, invoking *on_created* once an ID exists."""
        ...

    @abstractmethod
    async def connect(
        self,
        sandbox_id: str,
        *,
        workdir: str | None = None,
        user_sandbox_id: str | None = None,
        run_identity: RunIdentity | None = None,
    ) -> Sandbox:
        """Attach to an existing running instance by provider *sandbox_id*."""
        ...

    async def connect_observer(
        self,
        sandbox_id: str,
        *,
        timeout_seconds: int = 30,
    ) -> Sandbox:
        """Attach a short-timeout observer handle for background command recovery."""
        del timeout_seconds
        return await self.connect(sandbox_id)

    async def resume(
        self,
        sandbox_id: str,
        *,
        workdir: str | None = None,
        user_sandbox_id: str | None = None,
        run_identity: RunIdentity | None = None,
    ) -> Sandbox:
        """Resume a paused instance and return an attached handle."""
        del sandbox_id, workdir, user_sandbox_id, run_identity
        raise SandboxCapabilityUnsupportedError("pause", self.name)

    @abstractmethod
    async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
        """Inspect remote instance state, returning ``None`` when absent (404)."""
        ...

    async def pause(self, sandbox_id: str) -> None:
        """Suspend an active instance preserving state."""
        del sandbox_id
        raise SandboxCapabilityUnsupportedError("pause", self.name)

    @abstractmethod
    async def terminate(self, sandbox_id: str) -> None:
        """Destroy an instance idempotently (missing instance succeeds)."""
        ...

    async def renew_ttl(self, sandbox_id: str, ttl_seconds: int) -> None:
        """Extend provider-side lifetime when supported."""
        del sandbox_id, ttl_seconds
        return

    def build_panel_upstream(
        self,
        *,
        sandbox_id: str,
        port: int,
        path: str,
        query: str,
    ) -> PanelUpstream:
        """Construct HTTP/WS upstream connection specs for ``/sandbox-panel``."""
        del sandbox_id, port, path, query
        raise SandboxCapabilityUnsupportedError("panel", self.name)

    async def reset_rootfs(self, sandbox_id: str) -> None:
        """Reset container rootfs overlay while keeping persistent storage."""
        del sandbox_id
        raise SandboxCapabilityUnsupportedError("reset_rootfs", self.name)

    async def aclose(self) -> None:
        """Release any provider-owned pooled clients during shutdown."""
        return
