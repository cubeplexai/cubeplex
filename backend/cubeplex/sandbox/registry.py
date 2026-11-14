"""Sandbox provider registry and default resolution."""

from __future__ import annotations

from collections.abc import Mapping

from cubeplex.config import config
from cubeplex.sandbox.base import SandboxError
from cubeplex.sandbox.provider import SandboxProvider
from cubeplex.sandbox.providers.local import LocalSandboxProvider
from cubeplex.sandbox.providers.opensandbox import OpenSandboxProvider


class UnknownSandboxProviderError(SandboxError, LookupError):
    """Raised when a caller requests a sandbox provider that is not registered."""


class ProviderRegistry:
    """Lookup table of configured ``SandboxProvider`` implementations."""

    def __init__(
        self,
        providers: Mapping[str, SandboxProvider] | None = None,
        *,
        default_name: str | None = None,
    ) -> None:
        if providers is None:
            builtin: list[SandboxProvider] = [
                OpenSandboxProvider(),
                LocalSandboxProvider(),
            ]
            self._providers: dict[str, SandboxProvider] = {p.name: p for p in builtin}
        else:
            self._providers = dict(providers)
        self._default_name = default_name

    def _configured_default_name(self) -> str:
        if self._default_name is not None:
            return self._default_name
        raw = config.get("sandbox.provider", "opensandbox")
        text = str(raw or "").strip()
        return text or "opensandbox"

    def default(self) -> SandboxProvider:
        """Return the configured default sandbox provider."""
        return self.get(self._configured_default_name())

    def get(self, name: str | None = None) -> SandboxProvider:
        """Resolve *name*, falling back to ``default()`` when ``name`` is ``None``."""
        target = self._configured_default_name() if name is None else name
        if target not in self._providers:
            available = ", ".join(sorted(self._providers)) or "<none>"
            raise UnknownSandboxProviderError(
                f"Unknown sandbox provider {target!r} (registered: {available})"
            )
        return self._providers[target]

    def register(self, provider: SandboxProvider) -> None:
        """Register or replace a provider instance under ``provider.name``."""
        self._providers[provider.name] = provider

    def all(self) -> tuple[SandboxProvider, ...]:
        """Return all registered provider instances."""
        return tuple(self._providers.values())

    async def aclose(self) -> None:
        """Close all registered providers cleanly."""
        seen: set[int] = set()
        for provider in self._providers.values():
            ident = id(provider)
            if ident in seen:
                continue
            seen.add(ident)
            await provider.aclose()


_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    """Return the process-wide ``ProviderRegistry`` singleton."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def reset_provider_registry() -> None:
    """Clear the cached ``ProviderRegistry`` singleton (intended for tests)."""
    global _registry
    _registry = None
