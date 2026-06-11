"""Parser: Tempo OTLP JSON → TraceDetail."""

import json
from pathlib import Path

import pytest

from cubebox.api.schemas.trace import SpanKind
from cubebox.services.tempo_client import parse_trace_detail

FIXTURES = Path(__file__).parent.parent / "fixtures" / "tempo"


@pytest.fixture
def multi_turn_json() -> dict:
    return json.loads((FIXTURES / "sample_trace_multi_turn.json").read_text())


@pytest.fixture
def oneshot_json() -> dict:
    return json.loads((FIXTURES / "sample_trace_oneshot.json").read_text())


def test_multi_turn_tree_shape(multi_turn_json: dict) -> None:
    detail = parse_trace_detail(multi_turn_json)
    assert detail.root.kind == SpanKind.AGENT
    assert detail.root.name == "invoke_agent"
    turns = [c for c in detail.root.children if c.kind == SpanKind.TURN]
    assert len(turns) >= 2
    for turn in turns:
        chat_children = [c for c in turn.children if c.kind == SpanKind.CHAT]
        assert len(chat_children) >= 1


def test_summary_extracts_business_ids(multi_turn_json: dict) -> None:
    detail = parse_trace_detail(multi_turn_json)
    s = detail.summary
    assert s.org_id and s.org_id.startswith("org-")
    assert s.workspace_id and s.workspace_id.startswith("ws-")
    assert s.user_id and s.user_id.startswith("usr-")
    assert s.conversation_id and s.conversation_id.startswith("conv-")
    assert s.run_id
    assert s.duration_ms > 0
    assert s.span_count == _count(detail.root)


def test_oneshot_collapses_to_single_chat(oneshot_json: dict) -> None:
    detail = parse_trace_detail(oneshot_json)
    assert detail.root.kind == SpanKind.AGENT
    leaves = _leaves(detail.root)
    assert any(n.kind == SpanKind.CHAT for n in leaves)


def _count(node) -> int:
    return 1 + sum(_count(c) for c in node.children)


def _leaves(node) -> list:
    if not node.children:
        return [node]
    out = []
    for c in node.children:
        out.extend(_leaves(c))
    return out
