"""Protocol conformance tests for OpDispatcher."""

from __future__ import annotations

from typing import Any

import pytest

from cubeplex.im.op_dispatcher import OpDispatcher


class FakeDispatcher:
    """Minimal OpDispatcher for protocol conformance testing."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def dispatch_create(self, state: Any) -> bool:
        self.calls.append("create")
        return True

    async def dispatch_stream(self, state: Any, text: str) -> bool:
        self.calls.append(f"stream:{text[:20]}")
        return True

    async def dispatch_patch(self, state: Any) -> bool:
        self.calls.append("patch")
        return True

    async def dispatch_finalize(self, state: Any) -> bool:
        self.calls.append("finalize")
        return True

    async def emergency_text(self, text: str) -> None:
        self.calls.append(f"emergency:{text[:20]}")

    async def aclose(self) -> None:
        self.calls.append("aclose")


def test_fake_is_op_dispatcher() -> None:
    d = FakeDispatcher()
    assert isinstance(d, OpDispatcher)


@pytest.mark.asyncio
async def test_fake_dispatch_create() -> None:
    d = FakeDispatcher()
    result = await d.dispatch_create(None)
    assert result is True
    assert d.calls == ["create"]
