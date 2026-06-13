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
    op2 = fold_event(
        {
            "type": "artifact",
            "data": {
                "action": "updated",
                "artifact": {"id": "art_1", "artifact_type": "image", "name": "x.png"},
            },
        },
        state,
        now=1.0,
    )
    assert op2 is not None
    assert op2.kind == "patch_card"
    assert len(state.card_state.artifacts) == 1


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
