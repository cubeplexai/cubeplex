"""Unit tests for AttachmentHydrator (mocked sandbox + objectstore)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.agents.hydrator import (
    AttachmentHydrationError,
    AttachmentHydrator,
)
from cubeplex.models import Attachment


def _att(**kwargs: object) -> Attachment:
    return Attachment(  # type: ignore[call-arg]
        id=str(kwargs.get("id", "fid1")),
        org_id=str(kwargs.get("org_id", "org1")),
        workspace_id=str(kwargs.get("workspace_id", "ws1")),
        conversation_id=str(kwargs.get("conversation_id", "conv1")),
        uploader_user_id="u1",
        filename=str(kwargs.get("filename", "a.png")),
        mime_type="image/png",
        size_bytes=10,
        kind=str(kwargs.get("kind", "document")),
        object_key=str(kwargs.get("object_key", "k1")),
        sandbox_path=str(kwargs.get("sandbox_path", "/workspace/uploads/conv1/fid1/a.png")),
        status="pending",
    )


@pytest.mark.asyncio
async def test_hydrate_skips_when_file_exists() -> None:
    sandbox = MagicMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="EXISTS", exit_code=0))
    sandbox.upload = AsyncMock()
    objectstore = MagicMock()
    objectstore.download_file = AsyncMock()
    repo = MagicMock()
    repo.get_in_conversation = AsyncMock(return_value=_att())

    h = AttachmentHydrator(repo=repo, sandbox=sandbox, objectstore=objectstore)
    out = await h.hydrate(conversation_id="conv1", file_ids=["fid1"])

    assert out == {"fid1": "/workspace/uploads/conv1/fid1/a.png"}
    objectstore.download_file.assert_not_called()
    sandbox.upload.assert_not_called()


@pytest.mark.asyncio
async def test_hydrate_downloads_when_missing() -> None:
    sandbox = MagicMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="MISSING", exit_code=0))
    sandbox.upload = AsyncMock()
    objectstore = MagicMock()
    objectstore.download_file = AsyncMock(return_value=(b"\x89PNG...", "image/png"))
    repo = MagicMock()
    repo.get_in_conversation = AsyncMock(return_value=_att())

    h = AttachmentHydrator(repo=repo, sandbox=sandbox, objectstore=objectstore)
    await h.hydrate(conversation_id="conv1", file_ids=["fid1"])

    objectstore.download_file.assert_awaited_once_with("k1")
    sandbox.upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_hydrate_raises_when_attachment_not_found() -> None:
    sandbox = MagicMock()
    sandbox.execute = AsyncMock()
    objectstore = MagicMock()
    repo = MagicMock()
    repo.get_in_conversation = AsyncMock(return_value=None)

    h = AttachmentHydrator(repo=repo, sandbox=sandbox, objectstore=objectstore)
    with pytest.raises(AttachmentHydrationError) as ei:
        await h.hydrate(conversation_id="conv1", file_ids=["missing"])
    assert ei.value.file_id == "missing"


@pytest.mark.asyncio
async def test_hydrate_quotes_shell_metacharacters_in_path() -> None:
    """Filename shell metacharacters (``$()``, backticks, ``;``) must not run
    as commands when the hydrator probes for the file via ``sandbox.execute``.

    _safe_basename in AttachmentService preserves ``$``, backticks, ``(``,
    ``)`` etc., so the only safe form here is shlex.quote — bash double-quotes
    still expand $() and backticks.
    """
    evil_path = "/workspace/uploads/conv1/fid1/$(touch /tmp/pwned)`whoami`.xlsx"
    sandbox = MagicMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="EXISTS", exit_code=0))
    sandbox.upload = AsyncMock()
    objectstore = MagicMock()
    repo = MagicMock()
    repo.get_in_conversation = AsyncMock(return_value=_att(sandbox_path=evil_path))

    h = AttachmentHydrator(repo=repo, sandbox=sandbox, objectstore=objectstore)
    await h.hydrate(conversation_id="conv1", file_ids=["fid1"])

    (cmd,), _ = sandbox.execute.call_args
    # The dangerous tokens must appear inside a single-quoted argument, which
    # bash treats as a literal — never inside double quotes (which would still
    # expand $() and backticks).
    assert "'/workspace/uploads/conv1/fid1/$(touch /tmp/pwned)`whoami`.xlsx'" in cmd
    assert '"' not in cmd.split("&&")[0]  # the test -f arg never sees a double quote


@pytest.mark.asyncio
async def test_hydrate_raises_on_objectstore_error() -> None:
    sandbox = MagicMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="MISSING", exit_code=0))
    sandbox.upload = AsyncMock()
    objectstore = MagicMock()
    objectstore.download_file = AsyncMock(side_effect=RuntimeError("rustfs down"))
    repo = MagicMock()
    repo.get_in_conversation = AsyncMock(return_value=_att())

    h = AttachmentHydrator(repo=repo, sandbox=sandbox, objectstore=objectstore)
    with pytest.raises(AttachmentHydrationError) as ei:
        await h.hydrate(conversation_id="conv1", file_ids=["fid1"])
    assert ei.value.file_id == "fid1"
    assert "rustfs down" in str(ei.value)


@pytest.mark.asyncio
async def test_hydrate_skips_images_without_touching_sandbox() -> None:
    """User-uploaded images are read from ObjectStore; hydrating them would
    cold-start a LazySandbox before the LLM turn starts."""
    sandbox = MagicMock()
    sandbox.execute = AsyncMock()
    sandbox.upload = AsyncMock()
    objectstore = MagicMock()
    objectstore.download_file = AsyncMock()
    repo = MagicMock()
    repo.get_in_conversation = AsyncMock(return_value=_att(kind="image"))

    h = AttachmentHydrator(repo=repo, sandbox=sandbox, objectstore=objectstore)
    out = await h.hydrate(conversation_id="conv1", file_ids=["fid1"])

    assert out == {}
    sandbox.execute.assert_not_called()
    sandbox.upload.assert_not_called()
    objectstore.download_file.assert_not_called()


@pytest.mark.asyncio
async def test_hydrate_images_do_not_block_document_staging() -> None:
    sandbox = MagicMock()
    sandbox.execute = AsyncMock(return_value=MagicMock(output="MISSING", exit_code=0))
    sandbox.upload = AsyncMock()
    objectstore = MagicMock()
    objectstore.download_file = AsyncMock(return_value=(b"%PDF", "application/pdf"))
    image = _att(id="img1", kind="image", filename="a.png")
    document = _att(
        id="doc1",
        kind="document",
        filename="b.pdf",
        sandbox_path="/workspace/uploads/conv1/doc1/b.pdf",
        object_key="k-doc",
    )

    async def _get(*, conversation_id: str, attachment_id: str) -> Attachment:
        del conversation_id
        return image if attachment_id == "img1" else document

    repo = MagicMock()
    repo.get_in_conversation = _get

    h = AttachmentHydrator(repo=repo, sandbox=sandbox, objectstore=objectstore)
    out = await h.hydrate(conversation_id="conv1", file_ids=["img1", "doc1"])

    assert out == {"doc1": "/workspace/uploads/conv1/doc1/b.pdf"}
    sandbox.execute.assert_awaited_once()
    objectstore.download_file.assert_awaited_once_with("k-doc")
    sandbox.upload.assert_awaited_once()
