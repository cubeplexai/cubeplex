"""IMArtifactDispatcher mutates card_state.artifacts instead of sending messages."""

from __future__ import annotations

from typing import Any

import pytest

from cubebox.im.artifacts import IMArtifactDispatcher
from cubebox.im.feishu.card_model import ArtifactItem, CardState


class _FakeConnector:
    def __init__(self) -> None:
        self.uploaded: list[str] = []

    async def upload_image(self, local_path: str) -> str | None:
        self.uploaded.append(local_path)
        return "img_v1_uploaded"


async def _fake_mint(**_: Any) -> str:
    return "nonce_1"


def _new_dispatcher(state: CardState, conn: _FakeConnector) -> IMArtifactDispatcher:
    return IMArtifactDispatcher(
        connector=conn,
        redis=None,
        redis_key_prefix="cb-",
        public_base_url="https://example.com",
        org_id="org_1",
        workspace_id="ws_1",
        conversation_id="cv_1",
        card_state=state,
        mint_share_token_fn=_fake_mint,
    )


@pytest.mark.asyncio
async def test_document_artifact_writes_share_url_to_card_state() -> None:
    state = CardState(bot_name="cubebox", run_id="run_1")
    state.artifacts.append(ArtifactItem(id="art_1", artifact_type="document", name="r.pdf"))
    conn = _FakeConnector()
    disp = _new_dispatcher(state, conn)
    await disp.handle({"id": "art_1", "artifact_type": "document", "name": "r.pdf", "version": 1})
    art = next(a for a in state.artifacts if a.id == "art_1")
    assert art.share_url is not None
    assert "https://example.com" in art.share_url
    assert "nonce_1" in art.share_url


@pytest.mark.asyncio
async def test_dispatcher_creates_missing_artifact_row() -> None:
    """fold_event normally creates the row; this guards against an artifact
    event arriving without a prior fold_event call (e.g., out-of-order)."""
    state = CardState(bot_name="cubebox", run_id="run_1")
    conn = _FakeConnector()
    disp = _new_dispatcher(state, conn)
    await disp.handle({"id": "art_new", "artifact_type": "document", "name": "x.pdf", "version": 1})
    assert len(state.artifacts) == 1
    assert state.artifacts[0].share_url is not None


@pytest.mark.asyncio
async def test_dispatcher_skips_when_id_missing() -> None:
    state = CardState(bot_name="cubebox", run_id="run_1")
    conn = _FakeConnector()
    disp = _new_dispatcher(state, conn)
    await disp.handle({"artifact_type": "document", "name": "x.pdf"})
    assert state.artifacts == []


@pytest.mark.asyncio
async def test_dispatcher_skips_share_url_when_base_url_invalid() -> None:
    state = CardState(bot_name="cubebox", run_id="run_1")
    state.artifacts.append(ArtifactItem(id="art_1", artifact_type="document", name="r.pdf"))
    conn = _FakeConnector()
    disp = IMArtifactDispatcher(
        connector=conn,
        redis=None,
        redis_key_prefix="cb-",
        public_base_url="",  # invalid — not http(s)://
        org_id="org_1",
        workspace_id="ws_1",
        conversation_id="cv_1",
        card_state=state,
        mint_share_token_fn=_fake_mint,
    )
    await disp.handle({"id": "art_1", "artifact_type": "document", "name": "r.pdf", "version": 1})
    art = next(a for a in state.artifacts if a.id == "art_1")
    assert art.share_url is None
