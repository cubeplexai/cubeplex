"""DoneEvent carries accumulated turn usage."""

from cubeplex.agents.schemas import DoneEvent, UsageEvent


def test_done_event_accepts_usage_payload() -> None:
    """DoneEvent.data can carry a usage dict."""
    done = DoneEvent(
        timestamp="2026-05-11T00:00:00Z",
        data={
            "usage": {
                "turn": {
                    "input_tokens": 200,
                    "output_tokens": 50,
                    "cache_read_tokens": 150,
                    "cache_write_tokens": 30,
                },
                "session": {
                    "total_input_tokens": 1000,
                    "total_output_tokens": 400,
                },
                "context_window": 128000,
            }
        },
    )
    assert done.data["usage"]["turn"]["input_tokens"] == 200
    assert done.data["usage"]["context_window"] == 128000


def test_usage_event_accumulation() -> None:
    """Verify that multiple UsageEvent payloads can be summed."""
    events = [
        UsageEvent(
            timestamp="t1",
            data={
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 80,
                "cache_write_tokens": 10,
            },
        ),
        UsageEvent(
            timestamp="t2",
            data={
                "input_tokens": 50,
                "output_tokens": 30,
                "cache_read_tokens": 40,
                "cache_write_tokens": 5,
            },
        ),
    ]
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    for e in events:
        for key in totals:
            totals[key] += e.data.get(key, 0)

    assert totals == {
        "input_tokens": 150,
        "output_tokens": 50,
        "cache_read_tokens": 120,
        "cache_write_tokens": 15,
    }
