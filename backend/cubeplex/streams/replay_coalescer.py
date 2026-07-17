"""Fold a run's replayed event backlog into a compact, order-preserving set.

On reconnect the backend replays the whole Redis event stream for the active
run. A long run emits tens of thousands of 1-character ``text_delta`` /
``tool_call_delta`` events, which the frontend folds O(N²) on the main thread
— the cause of the freeze-on-reload. This coalescer merges *adjacent*
same-key deltas so the replayed payload shrinks to roughly message
granularity, while preserving SSE order (the frontend applies replayed events
in order and dedups by ``event_id``).

Two performance invariants:
- Accumulation is O(N): delta pieces go into a list and are ``"".join()``ed
  once when the run is emitted — never ``s = s + delta`` per event (that
  copies the whole growing string each time, re-introducing the backend stall).
- A run is also flushed when it reaches ``MAX_COALESCED_CHARS``, so one
  enormous message becomes several bounded ``text_delta`` events. This keeps
  each join + each frontend reducer/render step bounded and lets the
  frontend's count-based yield fire (a single huge event would slip it).

Streaming and stateful so it can fold across read chunks without holding the
whole backlog in memory. Pure (no IO).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from cubeplex.streams.run_events import RunEvent

# Max accumulated characters in one coalesced delta before it is flushed and a
# fresh same-key run continues. ~64 KiB: large enough that normal messages
# stay a single event, small enough that each join/render step is cheap.
MAX_COALESCED_CHARS = 65536

# Mergeable event type -> the data field whose value accumulates.
_DELTA_FIELD = {
    "text_delta": "content",
    "reasoning": "content",
    "tool_call_delta": "args_delta",
}


def _merge_key(event: RunEvent) -> tuple[Any, ...] | None:
    """Coalescing key for a mergeable event, or None when not mergeable."""
    payload = event.payload
    etype = payload.get("type")
    if etype in ("text_delta", "reasoning"):
        return (etype, payload.get("agent_id"))
    if etype == "tool_call_delta":
        data = payload.get("data") or {}
        return (etype, payload.get("agent_id"), data.get("index"))
    return None


@dataclass(slots=True)
class _Pending:
    """An in-progress coalesced run. ``pieces`` are joined once at emit time."""

    key: tuple[Any, ...]
    payload: dict[str, Any]  # mutable copy of the first event's payload
    field: str  # "content" | "args_delta"
    pieces: list[str] = dc_field(default_factory=list)
    size: int = 0
    last_event_id: str = ""


class ReplayCoalescer:
    """Folds adjacent same-key deltas; flushes on key change or size cap.

    Holds at most one pending mergeable run. ``feed`` returns the events that
    are now complete (the pending run is held back until a differing event, or
    the size cap, forces a flush); ``flush`` emits whatever remains at end of
    replay.
    """

    def __init__(self, max_chars: int = MAX_COALESCED_CHARS) -> None:
        self._max_chars = max_chars
        self._pending: _Pending | None = None

    def feed(self, events: list[RunEvent]) -> list[RunEvent]:
        out: list[RunEvent] = []
        for event in events:
            key = _merge_key(event)
            if self._pending is not None and key == self._pending.key:
                self._absorb(event)
                if self._pending.size >= self._max_chars:
                    out.append(self._finish())  # emit a bounded chunk
                continue
            # Different key (or non-mergeable): flush the pending run first so
            # nothing is emitted out of stream order.
            if self._pending is not None:
                out.append(self._finish())
            if key is not None:
                self._start(event, key)
            else:
                out.append(event)
        return out

    def flush(self) -> list[RunEvent]:
        return [self._finish()] if self._pending is not None else []

    def _start(self, event: RunEvent, key: tuple[Any, ...]) -> None:
        payload = deepcopy(event.payload)
        fld = _DELTA_FIELD[payload["type"]]
        data = payload.setdefault("data", {})
        piece = data.get(fld) or ""
        self._pending = _Pending(
            key=key,
            payload=payload,
            field=fld,
            pieces=[piece],
            size=len(piece),
            last_event_id=event.event_id,
        )

    def _absorb(self, event: RunEvent) -> None:
        assert self._pending is not None
        edata = event.payload.get("data") or {}
        piece = edata.get(self._pending.field) or ""
        self._pending.pieces.append(piece)
        self._pending.size += len(piece)
        self._pending.last_event_id = event.event_id
        if self._pending.payload["type"] == "tool_call_delta":
            # Latest non-None identity wins (mirrors the route-level backfill).
            pdata = self._pending.payload["data"]
            if edata.get("tool_call_id") is not None:
                pdata["tool_call_id"] = edata["tool_call_id"]
            if edata.get("name") is not None:
                pdata["name"] = edata["name"]

    def _finish(self) -> RunEvent:
        assert self._pending is not None
        p = self._pending
        p.payload["data"][p.field] = "".join(p.pieces)
        # Stamp the last merged original id so frontend dedup stays monotonic.
        result = RunEvent(p.last_event_id, p.payload)
        self._pending = None
        return result
