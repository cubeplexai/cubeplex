"""Unit test: LazySandbox.download must not recreate the sandbox on failure.

A download failure is overwhelmingly a missing / unreadable path (e.g. the
agent referenced a skill file that isn't there). The previous behaviour nulled
and recreated the sandbox on ANY download error — which wiped /workspace AND
could not produce the file anyway, so a single bad path read destroyed the
agent's in-progress work and triggered recreate storms. The read error must
instead surface to the caller; a genuinely dead sandbox is recovered by the
next execute/upload call.
"""

from __future__ import annotations

import pytest

from cubebox.sandbox.lazy import LazySandbox
from cubebox.sandbox.manager import SandboxAttachment


class _FakeSandbox:
    def __init__(self, sandbox_id: str) -> None:
        self.id = sandbox_id

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        raise FileNotFoundError(paths[0])


class _CountingManager:
    """Counts get_or_create so the test can assert no recreate happened."""

    def __init__(self) -> None:
        self.create_count = 0

    async def get_or_create(self, **_kwargs: object) -> SandboxAttachment:
        self.create_count += 1
        fake = _FakeSandbox(f"sbx-{self.create_count}")
        return SandboxAttachment(sandbox=fake, user_sandbox_id=f"uss-{self.create_count}")  # type: ignore[arg-type]

    async def touch(self, *_a: object, **_k: object) -> None:
        return None

    async def renew_lease(self, *_a: object, **_k: object) -> None:
        return None


def _make_lazy(manager: _CountingManager) -> LazySandbox:
    return LazySandbox(
        manager=manager,  # type: ignore[arg-type]
        scope_type="user",
        scope_id="usr-1",
        user_id="usr-1",
        org_id="org-1",
        workspace_id="ws-1",
    )


@pytest.mark.asyncio
async def test_download_failure_does_not_recreate_sandbox() -> None:
    manager = _CountingManager()
    lazy = _make_lazy(manager)

    with pytest.raises(FileNotFoundError):
        await lazy.download(["/.skills/acme__x/1.0/missing.py"])

    # Exactly one create (the initial _ensure); the failed read must NOT have
    # triggered a recreate, and the live sandbox handle must be preserved.
    assert manager.create_count == 1
    assert lazy.initialized is True
