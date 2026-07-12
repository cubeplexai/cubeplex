"""Unit tests for the Sandbox pause/resume capability surface."""

import pytest

from cubeplex.sandbox.base import Sandbox
from cubeplex.sandbox.local import LocalSandbox


def test_local_sandbox_does_not_support_pause() -> None:
    sb = LocalSandbox()
    assert sb.supports_pause() is False


@pytest.mark.asyncio
async def test_base_pause_raises_not_implemented() -> None:
    sb = LocalSandbox()
    with pytest.raises(NotImplementedError):
        await sb.pause()


@pytest.mark.asyncio
async def test_connect_or_resume_default_raises() -> None:
    with pytest.raises(NotImplementedError):
        await Sandbox.connect_or_resume("sbx_x")
