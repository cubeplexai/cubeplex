"""Outbound rendering: fold run events into debounced IM ops + tail Redis.

The render fold is platform-agnostic. The tailer talks to a connector
through three lifecycle hooks (``on_processing_start`` / ``_complete`` /
``_failed``) and two send/edit primitives (``post_placeholder`` / ``edit``);
Feishu-vocabulary calls live in the connector, not here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from loguru import logger
from redis.asyncio import Redis

from cubebox.im.types import RenderState
from cubebox.streams.run_events import read_run_events_after

# Edit-interval ceiling under adaptive backoff. The default (0.8s) lives on
# ``RenderState.edit_interval`` itself so callers can override per-run; the
# constants here only govern flood-handling.
_EDIT_INTERVAL_MAX = 10.0
_MAX_FLOOD_STRIKES = 3


@dataclass(slots=True)
class OutboundOp:
    """One emitted action for the connector."""

    kind: str  # "post" | "edit" | "artifact" | "no_op"
    text: str = ""
    final: bool = False
    artifact: dict[str, Any] | None = None


def _composite_text(state: RenderState) -> str:
    parts: list[str] = []
    if state.tool_lines:
        parts.append("\n".join(state.tool_lines))
    if state.text_buffer:
        parts.append(state.text_buffer)
    return "\n\n".join(parts) if parts else "…"


def fold_event(event: dict[str, Any], state: RenderState, *, now: float) -> OutboundOp | None:
    """Fold one run event into the render state and emit zero-or-one op.

    Branches:
    - ``text_delta`` accumulates text; first delta emits ``post``, later
      deltas emit debounced ``edit`` ops (suppressed when
      ``state.edits_disabled``).
    - ``tool_call`` coalesces into a single italic line per tool name.
    - ``artifact`` emits ``kind="artifact"`` exactly once per artifact id
      for ``action="created"``; ``action="updated"`` always re-emits.
    - ``done`` / ``error`` are terminal; emit ``post`` instead of ``edit``
      when no placeholder has been posted yet (so a run that finishes
      before any text_delta still surfaces a final message).
    """
    etype = event.get("type")
    data = event.get("data") or {}

    if etype == "text_delta":
        state.text_buffer += data.get("content", "")
        if state.message_id is None:
            state.last_edit_monotonic = now
            return OutboundOp(kind="post", text=_composite_text(state))
        if state.edits_disabled:
            return None
        if now - state.last_edit_monotonic < state.edit_interval:
            return None
        state.last_edit_monotonic = now
        return OutboundOp(kind="edit", text=_composite_text(state))

    if etype == "tool_call":
        name = data.get("name", "tool")
        line = f"_running `{name}`…_"
        if line not in state.tool_lines:
            state.tool_lines.append(line)
        return None

    if etype == "artifact":
        artifact = data.get("artifact") or {}
        art_id = artifact.get("id", "")
        action = data.get("action", "created")
        if not art_id:
            return None
        already = art_id in state.posted_artifacts
        if already and action == "created":
            return None
        state.posted_artifacts.add(art_id)
        return OutboundOp(kind="artifact", artifact=artifact)

    if etype == "done":
        kind = "post" if state.message_id is None else "edit"
        return OutboundOp(kind=kind, text=_composite_text(state), final=True)

    if etype == "error":
        msg = data.get("message", "the run failed")
        kind = "post" if state.message_id is None else "edit"
        return OutboundOp(kind=kind, text=f"⚠️ error: {msg}", final=True)

    return None


def note_flood_strike(state: RenderState) -> None:
    """Tailer-side hook: connector signaled a flood-control response.

    Doubles the edit interval (up to 10s) and after ``_MAX_FLOOD_STRIKES``
    consecutive strikes permanently disables progressive edits — the final
    ``done`` / ``error`` op still emits one terminal post/edit so the user
    sees a complete answer even on a hot rate-limit run.
    """
    state.consecutive_flood_strikes += 1
    state.edit_interval = min(state.edit_interval * 2, _EDIT_INTERVAL_MAX)
    if state.consecutive_flood_strikes >= _MAX_FLOOD_STRIKES:
        state.edits_disabled = True


def note_edit_success(state: RenderState) -> None:
    """Tailer-side hook: a streaming edit succeeded — reset the strike counter."""
    state.consecutive_flood_strikes = 0


class OutboundRunTailer:
    """Tail a run's Redis event stream and emit ops via the connector.

    Lifecycle calls go through the connector's ``on_processing_start /
    _complete / _failed`` hooks — Feishu-specific reactions live in
    FeishuConnector, not here.

    The tailer also dispatches ``OutboundOp(kind="artifact")`` events to an
    optional artifact dispatcher; if none is given the events are dropped.
    """

    def __init__(
        self,
        *,
        redis: Redis,
        key_prefix: str,
        run_id: str,
        connector: Any,
        state: RenderState,
        artifact_dispatcher: Any | None = None,
        block_ms: int = 2000,
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._run_id = run_id
        self._connector = connector
        self._state = state
        self._artifact_dispatcher = artifact_dispatcher
        self._block_ms = block_ms

    async def run(self) -> None:
        """Tail until a terminal event arrives or the loop is cancelled."""
        try:
            await self._connector.on_processing_start(self._state)
        except Exception:
            logger.warning("on_processing_start raised; continuing", exc_info=True)

        last_id = "0"
        succeeded = False
        try:
            while True:
                events = await read_run_events_after(
                    self._redis,
                    prefix=self._prefix,
                    run_id=self._run_id,
                    last_event_id=last_id,
                    block_ms=self._block_ms,
                )
                if not events:
                    continue
                done = False
                for ev in events:
                    last_id = ev.event_id
                    op = fold_event(ev.payload, self._state, now=time.monotonic())
                    if op is None:
                        continue
                    if op.kind == "artifact" and op.artifact is not None:
                        if self._artifact_dispatcher is not None:
                            try:
                                await self._artifact_dispatcher.handle(op.artifact)
                            except Exception:
                                logger.warning("artifact dispatch failed", exc_info=True)
                        continue
                    if op.kind == "post":
                        try:
                            ts = await self._connector.post_placeholder(op.text)
                        except Exception:
                            logger.warning("post_placeholder failed", exc_info=True)
                            ts = None
                        if ts:
                            self._state.message_id = ts
                    elif op.kind == "edit":
                        try:
                            await self._connector.edit(self._state.message_id, op.text)
                            note_edit_success(self._state)
                        except _FloodSignal:
                            note_flood_strike(self._state)
                        except Exception:
                            logger.warning("edit failed", exc_info=True)
                    if op.final:
                        done = True
                        # The error branch in fold_event prepends "⚠️" to the
                        # text; success is "we never saw that marker".
                        if not op.text.startswith("⚠️"):
                            succeeded = True
                if done:
                    return
        finally:
            try:
                if succeeded:
                    await self._connector.on_processing_complete(self._state)
                else:
                    await self._connector.on_processing_failed(self._state)
            except Exception:
                logger.warning("on_processing_* hook raised", exc_info=True)


class _FloodSignal(Exception):
    """Marker the connector raises to tell the tailer to back off edits.

    Connectors translate platform-specific rate-limit responses into this
    typed exception so ``OutboundRunTailer`` can apply adaptive backoff
    without knowing each platform's quota codes.
    """
