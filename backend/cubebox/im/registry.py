"""Platform connector registry.

Each IM platform registers itself at import time via ``register_platform()``.
The runtime and worker look up connectors by ``account.platform`` via
``get_platform()``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PlatformConnector(Protocol):
    """Protocol that each IM platform implements."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any: ...

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any: ...

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None: ...

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None: ...


_registry: dict[str, PlatformConnector] = {}


def register_platform(name: str, connector: PlatformConnector) -> None:
    if name in _registry:
        raise ValueError(f"platform already registered: {name}")
    _registry[name] = connector


def get_platform(name: str) -> PlatformConnector:
    try:
        return _registry[name]
    except KeyError:
        raise KeyError(f"unknown IM platform: {name}") from None


def registered_platforms() -> list[str]:
    return list(_registry.keys())
