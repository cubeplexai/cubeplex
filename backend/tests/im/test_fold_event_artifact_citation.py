"""Tests for fold_event handling artifact and citation events.

Field names follow the Task 0 audit:
- artifact.data: {action, artifact: {id, artifact_type, name, ...}}
- citation.data: {citation_id, metadata: {url, title, ...}, chunks, tool_call_id}
"""

from cubebox.im.outbound import fold_event
from cubebox.im.types import RenderState


def _state_with_card() -> RenderState:
    s = RenderState(bot_name="cubebox", run_id="run_1")
    s.card_id = "AAQA"
    # Park last_patch_monotonic far in the past so tests don't trip the
    # patch_interval coalescer just by using small ``now=`` values.
    s.last_patch_monotonic = -1000.0
    return s


def test_artifact_created_upserts_into_card_state() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "created",
                "artifact": {
                    "id": "art_1",
                    "artifact_type": "document",
                    "name": "r.pdf",
                },
            },
        },
        state,
        now=0.0,
    )
    assert op is not None
    assert op.kind == "patch_card"
    assert len(state.card_state.artifacts) == 1
    art = state.card_state.artifacts[0]
    assert art.id == "art_1"
    assert art.artifact_type == "document"
    assert art.name == "r.pdf"


def test_artifact_created_idempotent_on_duplicate_id() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "created",
                "artifact": {"id": "art_1", "artifact_type": "image", "name": "x.png"},
            },
        },
        state,
        now=0.0,
    )
    op2 = fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "created",
                "artifact": {"id": "art_1", "artifact_type": "image", "name": "x.png"},
            },
        },
        state,
        now=1.0,
    )
    assert op2 is None
    assert len(state.card_state.artifacts) == 1


def test_artifact_updated_emits_op_even_if_already_present() -> None:
    state = _state_with_card()
    fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "created",
                "artifact": {"id": "art_1", "artifact_type": "image", "name": "x.png"},
            },
        },
        state,
        now=0.0,
    )
    # Wait long enough to clear the patch_interval coalescer.
    op2 = fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "updated",
                "artifact": {"id": "art_1", "artifact_type": "image", "name": "x.png"},
            },
        },
        state,
        now=10.0,
    )
    assert op2 is not None
    assert op2.kind == "patch_card"
    assert len(state.card_state.artifacts) == 1


def test_artifact_updated_refreshes_name_type_and_clears_render_fields() -> None:
    """Updates must overwrite the existing row. Stale name/type would mis-label
    it; stale image_key would keep rendering the old image after an
    image→html switch; stale share_url would point at a token minted for the
    old type. The dispatcher re-mints those after the patch lands.
    """
    state = _state_with_card()
    fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "created",
                "artifact": {"id": "art_1", "artifact_type": "image", "name": "old.png"},
            },
        },
        state,
        now=0.0,
    )
    # The dispatcher would normally fill these on the original create. Set them
    # manually so we can prove they get cleared on update.
    row = state.card_state.artifacts[0]
    row.image_key = "img_old"
    row.share_url = "https://example.com/old"
    row.description = "old description"

    op = fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "updated",
                "artifact": {
                    "id": "art_1",
                    "artifact_type": "html",
                    "name": "new.html",
                },
            },
        },
        state,
        now=10.0,
    )
    assert op is not None and op.kind == "patch_card"
    assert len(state.card_state.artifacts) == 1
    refreshed = state.card_state.artifacts[0]
    assert refreshed.name == "new.html"
    assert refreshed.artifact_type == "html"
    # Render fields cleared so the dispatcher re-mints them for the new type.
    assert refreshed.image_key is None
    assert refreshed.share_url is None
    assert refreshed.description is None


def test_artifact_patch_coalesces_within_patch_interval() -> None:
    """Two artifact events arriving within ``patch_interval`` only emit one
    patch_card — the second collapses to None. The state still mutates so the
    eventual finalize carries both rows.
    """
    state = _state_with_card()
    fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "created",
                "artifact": {"id": "art_a", "artifact_type": "doc", "name": "a"},
            },
        },
        state,
        now=10.0,
    )
    # Arrives 0.5s later — well within the default 1.5s patch_interval.
    op2 = fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "created",
                "artifact": {"id": "art_b", "artifact_type": "doc", "name": "b"},
            },
        },
        state,
        now=10.5,
    )
    assert op2 is None
    assert len(state.card_state.artifacts) == 2


def test_artifact_without_id_is_dropped() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "created",
                "artifact": {"artifact_type": "image", "name": "x.png"},
            },
        },
        state,
        now=0.0,
    )
    assert op is None
    assert state.card_state.artifacts == []


def test_artifact_when_no_card_emits_card_create() -> None:
    state = RenderState(bot_name="cubebox", run_id="run_1")
    op = fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "created",
                "artifact": {"id": "art_1", "artifact_type": "document", "name": "r.pdf"},
            },
        },
        state,
        now=0.0,
    )
    assert op is not None
    assert op.kind == "card_create"
    assert len(state.card_state.artifacts) == 1


def test_citation_updates_index_no_op_emitted() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "citation",
            "data": {
                "citation_id": "1",
                "chunks": [{"chunk_index": 0, "content": "..."}],
                "metadata": {"url": "https://example.com/a", "title": "Example"},
                "tool_call_id": "tc_1",
            },
        },
        state,
        now=0.0,
    )
    assert op is None
    assert state.card_state.citation_index["1"] == ("https://example.com/a", "Example")


def test_citation_without_url_dropped() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "citation",
            "data": {
                "citation_id": "1",
                "chunks": [],
                "metadata": {"title": "no url"},
                "tool_call_id": "tc_1",
            },
        },
        state,
        now=0.0,
    )
    assert op is None
    assert "1" not in state.card_state.citation_index


def test_citation_without_metadata_dropped() -> None:
    state = _state_with_card()
    op = fold_event(
        {
            "type": "citation",
            "data": {
                "citation_id": "1",
                "chunks": [],
                "tool_call_id": "tc_1",
            },
        },
        state,
        now=0.0,
    )
    assert op is None
    assert "1" not in state.card_state.citation_index
