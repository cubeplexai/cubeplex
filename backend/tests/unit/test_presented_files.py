"""Unit tests for present_file path normalization and tool wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cubeplex.middleware.artifacts import ArtifactMiddleware
from cubeplex.services.presented_files import (
    PresentedFilePathError,
    normalize_sandbox_path,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/workspace/tmp/a.png", "/workspace/tmp/a.png"),
        ("/workspace/./tmp/a.png", "/workspace/tmp/a.png"),
        ("/workspace/tmp/../tmp/a.png", "/workspace/tmp/a.png"),
        ("/workspace", "/workspace"),
    ],
)
def test_normalize_sandbox_path_ok(raw: str, expected: str) -> None:
    assert normalize_sandbox_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "  ",
        "relative/path.png",
        "/etc/passwd",
        "/workspace/../../etc/passwd",
        "/tmp/x.png",
        "/workspace\x00/evil",
    ],
)
def test_normalize_sandbox_path_rejects(raw: str) -> None:
    with pytest.raises(PresentedFilePathError):
        normalize_sandbox_path(raw)


def test_artifact_middleware_exposes_present_file() -> None:
    sandbox = MagicMock()
    mw = ArtifactMiddleware(
        sandbox=sandbox,
        conversation_id="conv-x",
        org_id="org-x",
        workspace_id="ws-x",
    )
    names = {t.name for t in mw.tools}
    assert names == {"save_artifact", "present_file"}


def test_artifact_prompt_mentions_present_file() -> None:
    from cubeplex.prompts.artifacts import ARTIFACT_PROMPT

    assert "present_file" in ARTIFACT_PROMPT
    assert "/workspace" in ARTIFACT_PROMPT


def test_clip_caption_truncates() -> None:
    from cubeplex.services.presented_files import _CAPTION_MAX_LEN, _clip_caption

    long = "x" * (_CAPTION_MAX_LEN + 50)
    clipped = _clip_caption(long)
    assert clipped is not None
    assert len(clipped) == _CAPTION_MAX_LEN


@pytest.mark.asyncio
async def test_assert_regular_file_size_rejects_too_large() -> None:
    from cubeplex.api.exceptions import AttachmentTooLargeError
    from cubeplex.services.presented_files import _assert_regular_file_size

    sandbox = MagicMock()

    async def _exec(command: str, **kwargs: object) -> object:
        del command, kwargs
        return type("R", (), {"output": "999999999", "exit_code": 0})()

    sandbox.execute = _exec
    with pytest.raises(AttachmentTooLargeError):
        await _assert_regular_file_size(sandbox, "/workspace/big.bin", max_bytes=100)


@pytest.mark.asyncio
async def test_assert_regular_file_size_rejects_escape() -> None:
    from cubeplex.services.presented_files import _assert_regular_file_size

    sandbox = MagicMock()

    async def _exec(command: str, **kwargs: object) -> object:
        del command, kwargs
        return type("R", (), {"output": "ESCAPE", "exit_code": 3})()

    sandbox.execute = _exec
    with pytest.raises(PresentedFilePathError, match="outside"):
        await _assert_regular_file_size(sandbox, "/workspace/link", max_bytes=1000)
