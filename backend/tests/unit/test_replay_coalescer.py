from cubeplex.streams.replay_coalescer import ReplayCoalescer
from cubeplex.streams.run_events import RunEvent


def _ev(event_id: str, etype: str, *, data=None, agent_id=None) -> RunEvent:
    return RunEvent(
        event_id=event_id,
        payload={
            "type": etype,
            "timestamp": "",
            "data": data or {},
            "agent_id": agent_id,
            "agent_name": None,
        },
    )


def _run(events):
    c = ReplayCoalescer()
    out = c.feed(events)
    out += c.flush()
    return out


def test_consecutive_text_delta_same_agent_merges():
    out = _run(
        [
            _ev("1-0", "text_delta", data={"content": "Hel"}),
            _ev("2-0", "text_delta", data={"content": "lo"}),
        ]
    )
    assert len(out) == 1
    assert out[0].payload["data"]["content"] == "Hello"
    assert out[0].event_id == "2-0"  # last merged id


def test_consecutive_reasoning_same_agent_merges():
    out = _run(
        [
            _ev("1-0", "reasoning", data={"content": "th"}),
            _ev("2-0", "reasoning", data={"content": "ink"}),
        ]
    )
    assert len(out) == 1
    assert out[0].payload["data"]["content"] == "think"


def test_tool_call_delta_merges_per_index_keeps_type():
    out = _run(
        [
            _ev(
                "1-0",
                "tool_call_delta",
                data={"index": 0, "args_delta": '{"a"', "name": "calc", "tool_call_id": "t1"},
            ),
            _ev(
                "2-0",
                "tool_call_delta",
                data={"index": 0, "args_delta": ":1}", "name": None, "tool_call_id": None},
            ),
        ]
    )
    assert len(out) == 1
    assert out[0].payload["type"] == "tool_call_delta"
    assert out[0].payload["data"]["args_delta"] == '{"a":1}'
    assert out[0].payload["data"]["tool_call_id"] == "t1"
    assert out[0].payload["data"]["name"] == "calc"


def test_structural_events_pass_through():
    events = [
        _ev("1-0", "tool_call", data={"tool_call_id": "t1", "name": "calc", "arguments": {}}),
        _ev("2-0", "tool_result", data={"tool_call_id": "t1", "content": "1"}),
        _ev("3-0", "usage", data={"input_tokens": 1}),
        _ev("4-0", "citation", data={}),
        _ev("5-0", "artifact", data={}),
        _ev("6-0", "injected_message", data={"content": "x", "steer_id": "s"}),
        _ev("7-0", "status", data={"phase": "x"}),
    ]
    out = _run(events)
    assert [e.event_id for e in out] == ["1-0", "2-0", "3-0", "4-0", "5-0", "6-0", "7-0"]


def test_interleave_preserves_stream_order():
    # main e1, subagent e2, main e3 -> e1, e2, e3 (never e1+e3 around e2)
    out = _run(
        [
            _ev("1-0", "text_delta", data={"content": "A"}, agent_id=None),
            _ev("2-0", "text_delta", data={"content": "B"}, agent_id="subagent:t1"),
            _ev("3-0", "text_delta", data={"content": "C"}, agent_id=None),
        ]
    )
    assert [e.event_id for e in out] == ["1-0", "2-0", "3-0"]
    assert out[0].payload["data"]["content"] == "A"
    assert out[1].payload["data"]["content"] == "B"
    assert out[2].payload["data"]["content"] == "C"


def test_text_interrupted_by_tool_call_splits():
    out = _run(
        [
            _ev("1-0", "text_delta", data={"content": "before"}),
            _ev("2-0", "tool_call", data={"tool_call_id": "t1", "name": "calc", "arguments": {}}),
            _ev("3-0", "text_delta", data={"content": "after"}),
        ]
    )
    assert [e.payload["type"] for e in out] == ["text_delta", "tool_call", "text_delta"]
    assert out[0].payload["data"]["content"] == "before"
    assert out[2].payload["data"]["content"] == "after"


def test_done_flushes_pending_then_passes_through():
    out = _run(
        [
            _ev("1-0", "text_delta", data={"content": "hi"}),
            _ev("2-0", "done", data={}),
        ]
    )
    assert [e.payload["type"] for e in out] == ["text_delta", "done"]
    assert out[0].payload["data"]["content"] == "hi"


def test_empty_input():
    assert _run([]) == []


def test_chunk_boundary_invariance():
    events = [
        _ev("1-0", "text_delta", data={"content": "A"}),
        _ev("2-0", "text_delta", data={"content": "B"}),
        _ev(
            "3-0",
            "tool_call_delta",
            data={"index": 0, "args_delta": "{", "name": "c", "tool_call_id": "t"},
        ),
        _ev(
            "4-0",
            "tool_call_delta",
            data={"index": 0, "args_delta": "}", "name": None, "tool_call_id": None},
        ),
        _ev("5-0", "done", data={}),
    ]
    whole = _run(events)
    # Feed split at every boundary; output must be identical.
    for split in range(len(events) + 1):
        c = ReplayCoalescer()
        chunked = c.feed(events[:split]) + c.feed(events[split:]) + c.flush()
        assert [(e.event_id, e.payload["type"], e.payload["data"]) for e in chunked] == [
            (e.event_id, e.payload["type"], e.payload["data"]) for e in whole
        ]


def test_size_cap_splits_huge_run_into_bounded_events():
    # 6 deltas of 4 chars (24 total) with a 10-char cap -> multiple bounded
    # text_delta events whose contents concatenate back to the original.
    c = ReplayCoalescer(max_chars=10)
    events = [_ev(f"{i}-0", "text_delta", data={"content": "abcd"}) for i in range(6)]
    out = c.feed(events) + c.flush()
    assert len(out) >= 2
    assert all(e.payload["type"] == "text_delta" for e in out)
    assert "".join(e.payload["data"]["content"] for e in out) == "abcd" * 6
    # No emitted chunk grossly exceeds the cap (cap + at most one delta).
    assert all(len(e.payload["data"]["content"]) <= 10 + 4 for e in out)
