"""Unit tests for IMArtifactDispatcher (Task 11).

The objectstore download path and mint_share_token are patched to keep the
test hermetic; the dispatcher is what we're verifying.
"""

import asyncio
from typing import Any

import pytest

from cubebox.im import artifacts as im_artifacts

pytestmark = pytest.mark.asyncio


class _RecordingConnector:
    def __init__(self) -> None:
        self.uploads: list[str] = []
        self.image_keys: list[str] = []
        self.texts: list[str] = []

    async def upload_image(self, local_path: str) -> str | None:
        self.uploads.append(local_path)
        return "img_key_fake"

    async def send_image_message(self, image_key: str) -> str | None:
        self.image_keys.append(image_key)
        return "om_image"

    async def send_text_message(self, text: str) -> str | None:
        self.texts.append(text)
        return "om_text"


async def test_image_artifact_uploads_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """image artifact_type → download + upload_image + send_image_message; no share link."""
    connector = _RecordingConnector()

    async def fake_download(self: Any, key: str) -> tuple[bytes, str]:
        assert "image-art" in key
        return b"pretend image bytes", "image/png"

    class _FakeStore:
        async def download_file(self, key: str) -> tuple[bytes, str]:
            return await fake_download(self, key)

    monkeypatch.setattr(im_artifacts, "get_objectstore_client", lambda: _FakeStore())

    async def fake_mint(**_kwargs: Any) -> str:
        raise AssertionError("share link must NOT be minted for image artifacts")

    monkeypatch.setattr(im_artifacts, "mint_share_token", fake_mint)

    dispatcher = im_artifacts.IMArtifactDispatcher(
        connector=connector,
        redis=object(),
        redis_key_prefix="kp",
        public_base_url="http://test",
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
    )
    await dispatcher.handle(
        {
            "id": "image-art",
            "name": "chart.png",
            "artifact_type": "image",
            "entry_file": "chart.png",
            "path": "/tmp/chart.png",
            "version": 1,
        }
    )

    assert connector.uploads, "upload_image must be called"
    assert connector.image_keys == ["img_key_fake"]
    assert connector.texts == []


async def test_non_image_artifact_posts_share_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """code/document/website etc. → mint_share_token + send_text_message."""
    connector = _RecordingConnector()

    minted_with: list[dict[str, Any]] = []

    async def fake_mint(**kwargs: Any) -> str:
        minted_with.append(kwargs)
        return "abcdef1234"

    monkeypatch.setattr(im_artifacts, "mint_share_token", fake_mint)
    # If anyone tries to call the objectstore for a non-image artifact, fail loud.

    def _no_store() -> Any:
        raise AssertionError("non-image artifact must not touch objectstore")

    monkeypatch.setattr(im_artifacts, "get_objectstore_client", _no_store)

    dispatcher = im_artifacts.IMArtifactDispatcher(
        connector=connector,
        redis=object(),
        redis_key_prefix="kp",
        public_base_url="https://cubebox.example",
        org_id="org-2",
        workspace_id="ws-2",
        conversation_id="conv-2",
    )
    await dispatcher.handle(
        {
            "id": "doc-art",
            "name": "report.md",
            "artifact_type": "document",
            "entry_file": "report.md",
            "path": "/x/report.md",
            "version": 2,
        }
    )

    assert minted_with and minted_with[0]["artifact_id"] == "doc-art"
    assert minted_with[0]["org_id"] == "org-2"
    assert minted_with[0]["workspace_id"] == "ws-2"
    assert minted_with[0]["conversation_id"] == "conv-2"
    assert minted_with[0]["version"] == 2

    assert len(connector.texts) == 1
    msg = connector.texts[0]
    assert "report.md" in msg
    assert "document" in msg
    assert "https://cubebox.example/api/v1/public/artifacts/share/abcdef1234" in msg


async def test_missing_artifact_id_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """An artifact event without an id is silently dropped; no calls."""
    connector = _RecordingConnector()

    async def boom_mint(**_kwargs: Any) -> str:
        raise AssertionError("mint must not be called")

    monkeypatch.setattr(im_artifacts, "mint_share_token", boom_mint)
    dispatcher = im_artifacts.IMArtifactDispatcher(
        connector=connector,
        redis=object(),
        redis_key_prefix="kp",
        public_base_url="http://test",
        org_id="org",
        workspace_id="ws",
        conversation_id="conv",
    )
    await dispatcher.handle({"artifact_type": "image"})
    await asyncio.sleep(0)  # yield to confirm no scheduled side effects
    assert connector.uploads == []
    assert connector.image_keys == []
    assert connector.texts == []


async def test_share_link_skipped_when_public_url_not_absolute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If api.public_url isn't configured (or is relative), posting a
    relative path into Feishu would produce an unopenable link. Skip the
    send + log instead of shipping a broken link the user can't click."""
    connector = _RecordingConnector()
    minted: list[Any] = []

    async def fake_mint(**_kwargs: Any) -> str:
        minted.append(_kwargs)
        return "should-not-mint"

    monkeypatch.setattr(im_artifacts, "mint_share_token", fake_mint)
    monkeypatch.setattr(
        im_artifacts, "get_objectstore_client", lambda: (_ for _ in ()).throw(AssertionError())
    )

    dispatcher = im_artifacts.IMArtifactDispatcher(
        connector=connector,
        redis=object(),
        redis_key_prefix="kp",
        public_base_url="",  # not configured
        org_id="org",
        workspace_id="ws",
        conversation_id="conv",
    )
    await dispatcher.handle(
        {
            "id": "doc-art",
            "name": "report.md",
            "artifact_type": "document",
            "entry_file": "report.md",
            "version": 1,
        }
    )
    assert connector.texts == [], "must not post a relative share-link"
    assert minted == [], "must not mint a token we're not going to use"


async def test_image_download_failure_falls_back_to_share_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If we can't get the image bytes (object-store hiccup), do not strand
    the user — fall back to a share link so they can still open the artifact."""
    connector = _RecordingConnector()

    class _BrokenStore:
        async def download_file(self, key: str) -> tuple[bytes, str]:
            raise RuntimeError("object store unreachable")

    monkeypatch.setattr(im_artifacts, "get_objectstore_client", lambda: _BrokenStore())

    async def fake_mint(**_kwargs: Any) -> str:
        return "fallback"

    monkeypatch.setattr(im_artifacts, "mint_share_token", fake_mint)

    dispatcher = im_artifacts.IMArtifactDispatcher(
        connector=connector,
        redis=object(),
        redis_key_prefix="kp",
        public_base_url="http://test",
        org_id="org",
        workspace_id="ws",
        conversation_id="conv",
    )
    await dispatcher.handle(
        {
            "id": "img-broken",
            "name": "x.png",
            "artifact_type": "image",
            "entry_file": "x.png",
            "version": 1,
        }
    )

    assert connector.uploads == []
    assert connector.image_keys == []
    assert connector.texts and "fallback" in connector.texts[0]
