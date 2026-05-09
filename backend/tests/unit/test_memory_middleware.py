"""MemoryMiddleware unit tests — message-rendering invariants.

Guards against regressions in `_render_messages_with_snapshots`:
- HumanMessage metadata (additional_kwargs, response_metadata, name) survives
  snapshot wrapping. Otherwise AttachmentHintMiddleware can't read
  attachments_meta and uploaded files vanish from the LLM prompt.
"""

from langchain_core.messages import AIMessage, HumanMessage

from cubebox.middleware.memory import MemoryMiddleware


def _make_middleware() -> MemoryMiddleware:
    # repo_factory is unused in this code path; pass a placeholder.
    return MemoryMiddleware(repo_factory=lambda: None)  # type: ignore[arg-type]


def test_render_preserves_additional_kwargs_when_snapshot_applies() -> None:
    mw = _make_middleware()
    msg = HumanMessage(
        content="Run the build.",
        id="msg-1",
        additional_kwargs={"attachments_meta": [{"path": "/tmp/foo.txt", "kind": "text"}]},
    )
    snapshot = {
        "captured_at": "2026-05-09T00:00:00+00:00",
        "memory_ids": ["mem-1"],
        "rendered_text": "<workspace_memory>\n- [procedure] Run E2E with pnpm.\n</workspace_memory>",
    }

    out = mw._render_messages_with_snapshots([msg], {"msg-1": snapshot})

    assert len(out) == 1
    rendered = out[0]
    assert isinstance(rendered, HumanMessage)
    # Memory snapshot prepended
    assert "workspace_memory" in rendered.content
    assert "Run the build." in rendered.content
    # attachments_meta preserved → AttachmentHintMiddleware can still find files
    assert rendered.additional_kwargs == {
        "attachments_meta": [{"path": "/tmp/foo.txt", "kind": "text"}]
    }
    assert rendered.id == "msg-1"


def test_render_preserves_metadata_for_historical_message() -> None:
    """Same invariant for non-current messages (historical replay path)."""
    mw = _make_middleware()
    history = HumanMessage(
        content="Old turn.",
        id="msg-old",
        additional_kwargs={"attachments_meta": [{"path": "/tmp/old.png", "kind": "image"}]},
    )
    current = HumanMessage(content="New turn.", id="msg-new")
    snapshots = {
        "msg-old": {"captured_at": "t1", "memory_ids": [], "rendered_text": "<personal_memory/>"}
    }

    out = mw._render_messages_with_snapshots(
        [history, AIMessage(content="reply"), current], snapshots
    )

    assert len(out) == 3
    rendered_history = out[0]
    assert isinstance(rendered_history, HumanMessage)
    assert rendered_history.additional_kwargs == {
        "attachments_meta": [{"path": "/tmp/old.png", "kind": "image"}]
    }
    # current message has no snapshot in `snapshots` → passed through unchanged
    assert out[2] is current


def test_render_passes_through_when_no_matching_snapshot() -> None:
    mw = _make_middleware()
    msg = HumanMessage(content="hi", id="msg-x", additional_kwargs={"attachments_meta": [{}]})
    out = mw._render_messages_with_snapshots([msg], {"some-other-id": {}})
    # No snapshot for this message → original returned untouched (identity)
    assert out[0] is msg
