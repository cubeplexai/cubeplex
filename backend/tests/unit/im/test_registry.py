from __future__ import annotations

from typing import Any

import pytest

from cubeplex.im.registry import (
    get_platform,
    register_platform,
)


class FakePlatform:
    """Minimal PlatformConnector implementation for testing."""

    def parse_inbound(self, raw: dict[str, Any]) -> Any:
        return None

    async def build_tailer(
        self, *, run_id: str, queue_item: Any, account: Any, **kwargs: Any
    ) -> Any:
        return None

    async def on_account_enabled(self, account: Any, **kwargs: Any) -> None:
        pass

    async def on_account_disabled(self, account: Any, **kwargs: Any) -> None:
        pass


def test_register_and_get() -> None:
    register_platform("test_plat", FakePlatform())
    connector = get_platform("test_plat")
    assert connector is not None
    assert isinstance(connector, FakePlatform)


def test_get_unknown_raises() -> None:
    with pytest.raises(KeyError, match="no_such_platform"):
        get_platform("no_such_platform")


def test_double_register_raises() -> None:
    register_platform("double_test", FakePlatform())
    with pytest.raises(ValueError, match="double_test"):
        register_platform("double_test", FakePlatform())
