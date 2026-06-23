"""Wire-up tests for ``_hydrate_attachments_into_sandbox`` in run_manager.

The hydrator class itself is covered by ``backend/tests/test_hydrator.py``.
These tests pin the contract that the run path actually invokes the hydrator
with the right arguments and never raises on failure.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubebox.streams.run_manager import (
    _build_attachment_content_blocks,
    _hydrate_attachments_into_sandbox,
)


@asynccontextmanager
async def _stub_session() -> AsyncGenerator[MagicMock, None]:
    yield MagicMock()


def _patches(hydrator_cls: MagicMock) -> Any:
    """Patch the four lazy imports inside _hydrate_attachments_into_sandbox."""
    return (
        patch("cubebox.agents.hydrator.AttachmentHydrator", hydrator_cls),
        patch("cubebox.db.engine.async_session_maker", _stub_session),
        patch("cubebox.objectstore.get_objectstore_client", MagicMock()),
        patch("cubebox.repositories.AttachmentRepository", MagicMock()),
    )


@pytest.mark.asyncio
async def test_skips_when_no_attachments() -> None:
    hydrator_cls = MagicMock()
    patches = _patches(hydrator_cls)
    for p in patches:
        p.start()
    try:
        await _hydrate_attachments_into_sandbox(
            org_id="o1",
            workspace_id="w1",
            conversation_id="c1",
            attachment_ids=[],
            sandbox=MagicMock(),
        )
    finally:
        for p in patches:
            p.stop()
    hydrator_cls.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_sandbox_is_none() -> None:
    hydrator_cls = MagicMock()
    patches = _patches(hydrator_cls)
    for p in patches:
        p.start()
    try:
        await _hydrate_attachments_into_sandbox(
            org_id="o1",
            workspace_id="w1",
            conversation_id="c1",
            attachment_ids=["fid1"],
            sandbox=None,
        )
    finally:
        for p in patches:
            p.stop()
    hydrator_cls.assert_not_called()


@pytest.mark.asyncio
async def test_calls_hydrator_with_attachment_ids() -> None:
    hydrate = AsyncMock()
    instance = MagicMock()
    instance.hydrate = hydrate
    hydrator_cls = MagicMock(return_value=instance)
    patches = _patches(hydrator_cls)
    for p in patches:
        p.start()
    try:
        await _hydrate_attachments_into_sandbox(
            org_id="o1",
            workspace_id="w1",
            conversation_id="c1",
            attachment_ids=["fid1", "fid2"],
            sandbox=MagicMock(),
        )
    finally:
        for p in patches:
            p.stop()

    hydrator_cls.assert_called_once()
    hydrate.assert_awaited_once_with(conversation_id="c1", file_ids=["fid1", "fid2"])


def _att_row(*, fid: str, kind: str, filename: str = "f") -> MagicMock:
    row = MagicMock()
    row.id = fid
    row.kind = kind
    row.filename = filename
    row.sandbox_path = f"/workspace/uploads/c1/{fid}/{filename}"
    row.size_bytes = 10
    row.width = None
    row.height = None
    return row


@pytest.mark.asyncio
async def test_build_blocks_drops_documents_when_sandbox_unavailable() -> None:
    """When sandbox is None, the file_read tool is not registered. Hint
    metadata for documents would tell the model to call a tool it can't see
    — drop those rows but keep image rows (view_images works without
    sandbox via direct ObjectStore reads).
    """
    rows = {
        "img1": _att_row(fid="img1", kind="image", filename="a.png"),
        "doc1": _att_row(fid="doc1", kind="document", filename="b.pdf"),
        "other1": _att_row(fid="other1", kind="other", filename="c.bin"),
    }
    repo = MagicMock()

    async def _get(*, conversation_id: str, attachment_id: str) -> MagicMock:
        del conversation_id
        return rows[attachment_id]

    repo.get_in_conversation = _get
    repo_cls = MagicMock(return_value=repo)

    @asynccontextmanager
    async def _ses() -> AsyncGenerator[MagicMock, None]:
        yield MagicMock()

    with (
        patch("cubebox.db.engine.async_session_maker", _ses),
        patch("cubebox.repositories.AttachmentRepository", repo_cls),
    ):
        blocks = await _build_attachment_content_blocks(
            org_id="o1",
            workspace_id="w1",
            conversation_id="c1",
            attachment_ids=["img1", "doc1", "other1"],
            sandbox_available=False,
        )

    kinds = [b["kind"] for b in blocks]
    assert kinds == ["image"]


@pytest.mark.asyncio
async def test_build_blocks_keeps_everything_when_sandbox_available() -> None:
    rows = {
        "img1": _att_row(fid="img1", kind="image", filename="a.png"),
        "doc1": _att_row(fid="doc1", kind="document", filename="b.pdf"),
    }
    repo = MagicMock()

    async def _get(*, conversation_id: str, attachment_id: str) -> MagicMock:
        del conversation_id
        return rows[attachment_id]

    repo.get_in_conversation = _get
    repo_cls = MagicMock(return_value=repo)

    @asynccontextmanager
    async def _ses() -> AsyncGenerator[MagicMock, None]:
        yield MagicMock()

    with (
        patch("cubebox.db.engine.async_session_maker", _ses),
        patch("cubebox.repositories.AttachmentRepository", repo_cls),
    ):
        blocks = await _build_attachment_content_blocks(
            org_id="o1",
            workspace_id="w1",
            conversation_id="c1",
            attachment_ids=["img1", "doc1"],
            sandbox_available=True,
        )

    kinds = sorted(b["kind"] for b in blocks)
    assert kinds == ["document", "image"]


@pytest.mark.asyncio
async def test_swallows_hydrate_failure() -> None:
    """A hydration failure must not abort the run — the LLM tells the user."""
    hydrate = AsyncMock(side_effect=RuntimeError("objectstore down"))
    instance = MagicMock()
    instance.hydrate = hydrate
    hydrator_cls = MagicMock(return_value=instance)
    patches = _patches(hydrator_cls)
    for p in patches:
        p.start()
    try:
        await _hydrate_attachments_into_sandbox(
            org_id="o1",
            workspace_id="w1",
            conversation_id="c1",
            attachment_ids=["fid1"],
            sandbox=MagicMock(),
        )
    finally:
        for p in patches:
            p.stop()

    hydrate.assert_awaited_once()
