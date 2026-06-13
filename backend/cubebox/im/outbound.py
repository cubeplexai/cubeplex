"""Outbound rendering: fold run events into debounced IM ops + tail Redis.

The render fold is platform-agnostic. The tailer talks to a connector
through three lifecycle hooks (``on_processing_start`` / ``_complete`` /
``_failed``) and two send/edit primitives (``post_placeholder`` / ``edit``);
Feishu-vocabulary calls live in the connector, not here.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger
from redis.asyncio import Redis

from cubebox.im.types import RenderState
from cubebox.streams.run_events import read_run_events_after

# Edit-interval ceiling under adaptive backoff. The default (0.8s) lives on
# ``RenderState.edit_interval`` itself so callers can override per-run; the
# constants here only govern flood-handling.
_EDIT_INTERVAL_MAX = 10.0
_MAX_FLOOD_STRIKES = 3

# Terminal-delivery retry schedule. The streaming edits during a run can be
# dropped on flood-control (the user sees the prior partial), but the
# terminal ``done`` / ``error`` text MUST land or the user is stuck on a
# stale bubble forever. Three tries with exponential backoff cover a
# typical Feishu rate-limit window; if all three fail we fall back to a
# fresh ``send_text_message`` (new bubble) which uses ``messages/create``
# rather than ``messages/update`` and has its own quota.
_TERMINAL_RETRY_DELAYS = (0.5, 1.5, 4.0)


OpKind = Literal[
    "card_create",
    "stream_text",
    "patch_card",
    "finalize",
    "no_op",
]


@dataclass(slots=True)
class OutboundOp:
    """One emitted action for the cardkit client."""

    kind: OpKind
    element_id: str | None = None
    text: str = ""
    final: bool = False


def fold_event(event: dict[str, Any], state: RenderState, *, now: float) -> OutboundOp | None:
    """Fold one cubepi run event into ``state.card_state``.

    Task 8 covers text_delta only. Tasks 9-11 add tool_call, tool_result,
    artifact, citation, ask_user_request, sandbox_confirm_request,
    sub-agent routing via agent_id, done, error.
    """
    if state.card_state.run_start_monotonic == 0.0:
        state.card_state.run_start_monotonic = now
    etype = event.get("type")
    data = event.get("data") or {}

    if etype == "text_delta":
        delta = str(data.get("content", ""))
        state.card_state.streaming_content += delta
        if state.card_id is None:
            state.last_stream_monotonic = now
            return OutboundOp(kind="card_create")
        if state.edits_disabled:
            return None
        if now - state.last_stream_monotonic < state.stream_interval:
            return None
        state.last_stream_monotonic = now
        return OutboundOp(kind="stream_text", element_id="streaming_content", text=delta)

    if etype == "tool_call":
        import json as _json

        from cubebox.im.feishu.card_model import SubAgentRow, ToolStep

        tool_id = str(data.get("tool_call_id") or "")
        name = str(data.get("name") or "tool")
        args_raw = data.get("arguments")
        if isinstance(args_raw, str):
            if not args_raw:
                args: dict[str, Any] = {}
            else:
                try:
                    decoded = _json.loads(args_raw)
                    args = decoded if isinstance(decoded, dict) else {"raw": decoded}
                except (ValueError, TypeError):
                    args = {"raw": args_raw}
        elif isinstance(args_raw, dict):
            args = args_raw
        else:
            args = {}

        agent_id = event.get("agent_id")
        if agent_id:
            # Sub-agent tool_call: route to SubAgentRow, do NOT add to main tool_steps.
            row = state.card_state.find_sub_agent(str(agent_id))
            if row is None:
                state.card_state.sub_agents.append(
                    SubAgentRow(
                        agent_id=str(agent_id),
                        name=str(event.get("agent_name") or "sub-agent"),
                        tool_count=1,
                    )
                )
            else:
                row.tool_count += 1
        else:
            if tool_id and state.card_state.find_tool(tool_id) is None:
                state.card_state.tool_steps.append(
                    ToolStep(id=tool_id, name=name, args=args, start_monotonic=now)
                )

        if state.card_id is None:
            return OutboundOp(kind="card_create")
        # Structural change — bypass patch_interval throttle.
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card")

    if etype == "tool_result":
        agent_id = event.get("agent_id")
        if agent_id:
            # Sub-agent tool_result is a no-op for v1.
            return None
        tool_id = str(data.get("tool_call_id") or "")
        step = state.card_state.find_tool(tool_id)
        if step is None:
            return None
        elapsed_ms = max(0, int((now - step.start_monotonic) * 1000))
        is_error = bool(data.get("is_error"))
        content = str(data.get("content") or "")
        if is_error:
            step.mark_failed(error=content, elapsed_ms=elapsed_ms)
        else:
            step.mark_succeeded(result=content, elapsed_ms=elapsed_ms)
        if state.card_id is None:
            return OutboundOp(kind="card_create")
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card")

    if etype == "artifact":
        from cubebox.im.feishu.card_model import ArtifactItem

        action = str(data.get("action") or "created")
        artifact = data.get("artifact") or {}
        art_id = str(artifact.get("id") or "")
        if not art_id:
            return None
        existing = next((a for a in state.card_state.artifacts if a.id == art_id), None)
        if existing is not None and action == "created":
            return None
        if existing is None:
            state.card_state.artifacts.append(
                ArtifactItem(
                    id=art_id,
                    artifact_type=str(artifact.get("artifact_type") or ""),
                    name=str(artifact.get("name") or art_id),
                )
            )
        if state.card_id is None:
            return OutboundOp(kind="card_create")
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card")

    if etype == "citation":
        citation_id = str(data.get("citation_id") or "")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            return None
        url = str(metadata.get("url") or "")
        title = str(metadata.get("title") or "")
        if citation_id and url:
            state.card_state.citation_index[citation_id] = (url, title)
        return None

    if etype == "ask_user_request":
        from cubebox.im.feishu.card_model import PendingInput

        question_id = str(data.get("question_id") or "")
        questions_list = data.get("questions") or []
        if questions_list and isinstance(questions_list, list):
            first = questions_list[0] if isinstance(questions_list[0], dict) else {}
        else:
            first = {}
        prompt = str(first.get("prompt") or "")
        more = len(questions_list) - 1 if len(questions_list) > 1 else 0
        if more > 0:
            prompt = f"{prompt}\n\n_(+{more} more question{'s' if more > 1 else ''})_"
        raw_options = first.get("options") or []
        choices: list[tuple[str, str]] = []
        if isinstance(raw_options, list):
            for opt in raw_options:
                if isinstance(opt, str) and opt:
                    choices.append((opt, "default"))
                elif isinstance(opt, dict):
                    key = str(opt.get("key") or opt.get("label") or "")
                    btn_type = str(opt.get("type") or "default")
                    if key:
                        choices.append((key, btn_type))
        if not choices:
            choices = [("ok", "primary")]
        state.card_state.pending_input = PendingInput(
            kind="ask_user",
            run_id=state.run_id,
            question=prompt,
            choices=choices,
            question_id=question_id,
        )
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card") if state.card_id else OutboundOp(kind="card_create")

    if etype == "sandbox_confirm_request":
        from cubebox.im.feishu.card_model import PendingInput

        question_id = str(data.get("question_id") or "")
        command = str(data.get("command") or "")
        prompt = "是否允许执行以下命令？"
        if command:
            prompt = f"{prompt}\n\n```bash\n{command}\n```"
        state.card_state.pending_input = PendingInput(
            kind="sandbox_confirm",
            run_id=state.run_id,
            question=prompt,
            choices=[("approve", "primary"), ("deny", "danger")],
            question_id=question_id,
        )
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card") if state.card_id else OutboundOp(kind="card_create")

    if etype in ("ask_user_resolved", "sandbox_confirm_resolved"):
        pending = state.card_state.pending_input
        if pending is None:
            return None
        if pending.question_id != str(data.get("question_id") or ""):
            return None
        cancelled = bool(data.get("cancelled"))
        timed_out = bool(data.get("timed_out"))
        if cancelled:
            resolved = "cancelled"
        elif timed_out:
            resolved = "timed_out"
        elif etype == "sandbox_confirm_resolved":
            resolved = str(data.get("decision") or "")
        else:
            resolved = "answered"
        pending.resolved_choice = resolved
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card") if state.card_id else OutboundOp(kind="card_create")

    if etype == "done":
        state.card_state.finalized = True
        elapsed_ms = max(0, int((now - state.card_state.run_start_monotonic) * 1000))
        state.card_state.elapsed_ms = elapsed_ms
        return OutboundOp(kind="finalize", final=True)

    if etype == "error":
        state.card_state.finalized = True
        state.card_state.error = str(data.get("message") or "the run failed")
        return OutboundOp(kind="finalize", final=True)

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
        cardkit: Any,
        artifact_dispatcher: Any | None = None,
        block_ms: int = 2000,
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._run_id = run_id
        self._connector = connector
        self._state = state
        self._cardkit = cardkit
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
                    # Task 8: dispatch is a no-op stub. Task 12 rewires this to
                    # the cardkit client and re-introduces terminal-delivery
                    # retries against the new op kinds (card_create /
                    # stream_text / patch_card / finalize).
                    delivered = await self._dispatch_op(op, is_terminal=op.final)
                    if op.final:
                        done = True
                        if delivered:
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

    async def _dispatch_op(self, op: OutboundOp, *, is_terminal: bool) -> bool:
        """Translate one OutboundOp into the matching CardKit call.

        Returns True iff the op was delivered. ``card_create`` failures
        flip ``state.card_unavailable`` and fall back to emergency-text
        bubbles so the user still sees an answer. ``stream_text`` /
        ``patch_card`` flood signals collapse to a False return — the
        next fold step rebuilds and the tailer retries.

        The ``_cardkit=None`` shim path used by ``app.py`` for the path-(a)
        transition (Task 12 ↔ Task 17) short-circuits every op to False so
        the legacy non-card path keeps working until CardKit is wired in.
        """
        _ = (is_terminal, _TERMINAL_RETRY_DELAYS, asyncio)
        from cubebox.im.feishu.card_renderer import render

        state = self._state
        cardkit = self._cardkit
        if cardkit is None:
            return False

        if op.kind == "card_create":
            if state.card_unavailable:
                return False
            card_json = render(state.card_state)
            try:
                card_id = await cardkit.create_entity(card_json)
            except Exception:
                logger.warning(
                    "[outbound] CardKit create_entity failed; engaging emergency text",
                    exc_info=True,
                )
                state.card_unavailable = True
                await self._emergency_text("⚠️ 飞书富文本渲染暂时不可用，结果将以文本展示")
                if state.card_state.streaming_content:
                    await self._emergency_text(state.card_state.streaming_content[:4000])
                return False
            state.card_id = card_id
            state.card_state.advance_seq()
            try:
                msg_id = await self._connector.send_card_init_message(card_id)
            except Exception:
                logger.warning("[outbound] send_card_init_message raised", exc_info=True)
                msg_id = None
            state.bot_message_id = msg_id
            return True

        if op.kind == "stream_text":
            if state.card_id is None or state.card_unavailable:
                return False
            seq = state.card_state.advance_seq()
            try:
                await cardkit.stream_text(
                    card_id=state.card_id,
                    element_id=op.element_id or "streaming_content",
                    content=op.text,
                    sequence=seq,
                )
                note_edit_success(state)
                return True
            except _FloodSignal:
                note_flood_strike(state)
                return False
            except Exception:
                logger.warning("[outbound] stream_text failed", exc_info=True)
                return False

        if op.kind == "patch_card":
            if state.card_id is None or state.card_unavailable:
                return False
            seq = state.card_state.advance_seq()
            try:
                await cardkit.patch_card(
                    card_id=state.card_id,
                    card_json=render(state.card_state),
                    sequence=seq,
                )
                note_edit_success(state)
                return True
            except _FloodSignal:
                # Coalesce — next event will rebuild and resend.
                return False
            except Exception:
                logger.warning("[outbound] patch_card failed", exc_info=True)
                return False

        if op.kind == "finalize":
            if state.card_id is None or state.card_unavailable:
                if state.card_state.error:
                    await self._emergency_text(f"⚠️ {state.card_state.error}")
                elif state.card_state.streaming_content:
                    await self._emergency_text(state.card_state.streaming_content[:4000])
                return False
            seq = state.card_state.advance_seq()
            return bool(
                await cardkit.finalize(
                    card_id=state.card_id,
                    card_json=render(state.card_state),
                    sequence=seq,
                )
            )

        return False

    async def _emergency_text(self, text: str) -> None:
        try:
            await self._connector._send_emergency_text(text)
        except Exception:
            logger.warning("[outbound] emergency text send failed", exc_info=True)


class _FloodSignal(Exception):
    """Marker the connector raises to tell the tailer to back off edits.

    Connectors translate platform-specific rate-limit responses into this
    typed exception so ``OutboundRunTailer`` can apply adaptive backoff
    without knowing each platform's quota codes.
    """
