"""Tests for safe_boundary."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from cubebox.middleware.compaction.boundary import safe_boundary


def _h(text: str) -> HumanMessage:
    return HumanMessage(content=text)


def _a(text: str = "", tool_calls: list[dict] | None = None) -> AIMessage:
    return AIMessage(content=text, tool_calls=tool_calls or [])


def _t(call_id: str, text: str = "ok") -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=call_id, name="x")


def test_keeps_recent_window_when_history_short():
    msgs = [_h("h1"), _a("a1")]
    # keep_recent=4, so nothing to compact; boundary is None
    assert safe_boundary(msgs, keep_recent=4) is None


def test_basic_boundary_lands_on_humanmessage():
    msgs = [_h("h1"), _a("a1"), _h("h2"), _a("a2"), _h("h3"), _a("a3")]
    # keep_recent=2 → tentative boundary = 4 → msgs[4] = _h("h3") ✓
    assert safe_boundary(msgs, keep_recent=2) == 4


def test_walks_back_to_humanmessage():
    msgs = [_h("h1"), _a("a1"), _h("h2"), _a("a2"), _h("h3"), _a("a3")]
    # keep_recent=3 → tentative boundary = 3 → msgs[3] = _a, walk back to msgs[2] = _h
    assert safe_boundary(msgs, keep_recent=3) == 2


def test_does_not_split_tool_call_pair():
    # h1 → a1(tool_calls=[t1]) → tool t1 → h2 → a2
    msgs = [
        _h("h1"),
        _a(tool_calls=[{"id": "t1", "name": "x", "args": {}}]),
        _t("t1"),
        _h("h2"),
        _a("a2"),
    ]
    # keep_recent=2 → tentative boundary = 3 → msgs[3] = _h("h2") — suffix [_h, _a] has
    # no orphan ToolMessage, safe.
    assert safe_boundary(msgs, keep_recent=2) == 3


def test_orphan_tool_message_walks_back():
    # If keep_recent=3, tentative boundary = 2 → msgs[2] = _t("t1") (orphan).
    # Must walk to msgs[1] = _a (not Human), then msgs[0] = _h. boundary=0 → None.
    msgs = [
        _h("h1"),
        _a(tool_calls=[{"id": "t1", "name": "x", "args": {}}]),
        _t("t1"),
        _h("h2"),
        _a("a2"),
    ]
    assert safe_boundary(msgs, keep_recent=3) is None


def test_skips_system_messages_in_search():
    msgs = [SystemMessage(content="sys"), _h("h1"), _a("a1"), _h("h2"), _a("a2")]
    # keep_recent=2 → tentative boundary = 3 → msgs[3] = _h("h2")
    assert safe_boundary(msgs, keep_recent=2) == 3


def test_walks_back_when_initial_message_not_human():
    msgs = [_a("a1"), _a("a2"), _h("h1"), _a("a3")]
    # keep_recent=1 → tentative boundary = 3, msgs[3]=_a, walk back to msgs[2]=_h ✓
    assert safe_boundary(msgs, keep_recent=1) == 2


def test_min_compact_size_too_small_returns_none():
    msgs = [_h("h1"), _a("a1"), _h("h2"), _a("a2")]
    # keep_recent=2, tentative boundary=2, msgs[2]=_h ✓ — but only 2 msgs in prefix.
    # min_compact=4 demands more, so refuse.
    assert safe_boundary(msgs, keep_recent=2, min_compact=4) is None
