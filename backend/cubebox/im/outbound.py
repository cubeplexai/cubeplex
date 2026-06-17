"""Outbound rendering: fold run events into debounced IM ops + tail Redis.

The render fold is platform-agnostic. The tailer talks to a connector
through three lifecycle hooks (``on_processing_start`` / ``_complete`` /
``_failed``) and delegates outbound ops (``card_create`` / ``stream_text``
/ ``patch_card`` / ``finalize``) to an injected ``OpDispatcher``;
platform-specific rendering lives in the dispatcher implementation
(e.g. ``FeishuOpDispatcher``), not here.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger
from redis.asyncio import Redis

from cubebox.im.types import RenderState
from cubebox.streams.run_events import read_run_events_after

# AskUser / SandboxConfirm card-button gating: when the tailer emits a
# pending_input op we bind the inbound sender's open_id to a Redis key so
# the webhook ingress can reject clicks from anyone else. The default
# (10 minutes) matches the spec §6.5 pending-input window; per-event
# overrides come from the cubepi event's ``timeout_seconds`` field, capped
# at 24h so a malformed event can't pin a Redis key forever.
_AWAITING_TTL_DEFAULT_SECONDS = 600
_AWAITING_TTL_MAX_SECONDS = 24 * 60 * 60

# After this many consecutive flood-control responses we permanently disable
# progressive patches for the rest of the run. The final ``done`` / ``error``
# patch still emits so the user sees a complete answer even on a hot
# rate-limit run.
_MAX_FLOOD_STRIKES = 3


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
        if not state.card_state.streaming_content:
            return None
        # Feishu's streaming_mode markdown element expects the FULL cumulative
        # text on every PUT — the platform diffs it against the previous push
        # and renders the typewriter increment client-side. Sending only the
        # delta would REPLACE the rendered content with just the delta (the
        # user would see the card cycle through tail fragments). See
        # https://open.feishu.cn/document/cardkit-v1/streaming-updates-openapi-overview
        return OutboundOp(
            kind="stream_text",
            element_id="streaming_content",
            text=state.card_state.streaming_content,
        )

    if etype == "tool_call":
        import json as _json

        from cubebox.im.card_model import SubAgentRow, ToolStep

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
        # Respect ``edits_disabled`` after repeated 230020 strikes; the
        # accumulated tool_steps land on the final ``done`` finalize.
        if state.edits_disabled:
            return None
        # Throttle bursty tool_call patches via state.patch_interval (default
        # 1.5s). A run with 20 concurrent tool calls would otherwise fire 20
        # full-card patches in a tight burst — enough to trip 230020 flood
        # control before ``edits_disabled`` engages. State still mutates so
        # the final ``done`` finalize carries every tool step. The first
        # tool_call after a quiet window passes through immediately so the
        # spinner appears promptly.
        if now - state.last_patch_monotonic < state.patch_interval:
            return None
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
        # After repeated 230020 flood strikes ``note_flood_strike`` sets
        # ``edits_disabled`` specifically to stop hammering CardKit.
        # tool_result keeps mutating ``state.card_state`` so the eventual
        # finalize carries the right state, but we suppress the per-result
        # patch_card op so tool-heavy runs don't fight the throttle. The
        # accumulated state lands when ``done`` triggers ``finalize``.
        if state.edits_disabled:
            return None
        # Throttle bursty tool_result patches via patch_interval (default 1.5s).
        # Tool-heavy runs with results arriving milliseconds apart would
        # otherwise emit one full-card patch per result, defeating the bucket
        # and tripping flood control. State still mutates so the eventual
        # finalize carries the full snapshot.
        if now - state.last_patch_monotonic < state.patch_interval:
            return None
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card")

    if etype == "artifact":
        from cubebox.im.card_model import ArtifactItem

        action = str(data.get("action") or "created")
        artifact = data.get("artifact") or {}
        art_id = str(artifact.get("id") or "")
        if not art_id:
            return None
        new_type = str(artifact.get("artifact_type") or "")
        new_name = str(artifact.get("name") or art_id)
        existing = next((a for a in state.card_state.artifacts if a.id == art_id), None)
        if existing is not None and action == "created":
            return None
        if existing is None:
            state.card_state.artifacts.append(
                ArtifactItem(id=art_id, artifact_type=new_type, name=new_name)
            )
        else:
            # action == "updated": refresh the row in-place. Stale name / type
            # would mis-label the artifact; stale image_key would keep
            # rendering the old image after an image→html switch; stale
            # share_url would point at a token minted for the old type. Drop
            # the post-create fields (share_url / image_key / description) so
            # IMArtifactDispatcher can re-mint them for the new payload.
            existing.artifact_type = new_type
            existing.name = new_name
            existing.share_url = None
            existing.image_key = None
            existing.description = None
        if state.card_id is None:
            return OutboundOp(kind="card_create")
        if state.edits_disabled:
            return None
        # Artifacts are usually emitted one at a time, but a batch creation
        # (e.g. a single tool call produces several files) can still burst.
        # Throttle on patch_interval like tool_call / tool_result.
        if now - state.last_patch_monotonic < state.patch_interval:
            return None
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
        from cubebox.im.card_model import PendingInput

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
        # cubepi expects the answer dict keyed by `questions[*].key`. v1
        # captures questions[0].key so the resume path can rebuild the
        # right shape; multi-question forms fall back to questions[0].
        answer_key = str(first.get("key") or "") or None
        raw_options = first.get("options") or []
        # Each choice is (label, value, button_type):
        # - label is what the user reads on the button (option's ``label``)
        # - value is what cubepi receives back in the answer dict (option's
        #   ``value`` — falling back to ``key`` for legacy fixtures, then
        #   ``label`` if neither is set)
        # - button_type is the Feishu styling hint
        # Keeping label and value separate matters for {label:"Yes", value:"yes"}
        # — sending "Yes" back would mismatch cubepi's schema.
        choices: list[tuple[str, str, str]] = []
        # multi_select=True questions need a list answer; a single Feishu card
        # button can only ship one scalar. The free-form fallback below
        # already routes to the web client — reuse it by skipping option
        # parsing so the renderer shows the notice instead of buttons that
        # would only send one of the N required selections.
        multi_select = bool(first.get("multi_select"))
        # Multi-QUESTION forms (questions list has 2+ entries) also can't be
        # answered via a single button row: the click would submit only
        # questions[0]'s answer and cubepi would reject or mis-resume the
        # incomplete form. Treat like free-form / multi-select and route to
        # the web client.
        multi_question = len(questions_list) > 1
        if isinstance(raw_options, list) and not multi_select and not multi_question:
            for opt in raw_options:
                if isinstance(opt, str) and opt:
                    # Bare-string options collapse: the same string is both
                    # the human label and the schema value.
                    choices.append((opt, opt, "default"))
                elif isinstance(opt, dict):
                    value = str(opt.get("value") or opt.get("key") or opt.get("label") or "")
                    label = str(opt.get("label") or opt.get("value") or opt.get("key") or "")
                    btn_type = str(opt.get("type") or "default")
                    if value:
                        choices.append((label, value, btn_type))
        # Free-form (no options) and multi-select questions cannot be answered
        # via a single card button. The old "OK" fallback was misleading: clicking
        # it sent ``{key: "ok"}`` which cubepi either rejected as a schema
        # mismatch or silently treated as the wrong value for a path/filename/date
        # prompt. v1 doesn't render text input via CardKit; append a notice and
        # leave ``choices`` empty so the renderer surfaces the "(等待响应)" hint
        # instead of a bogus button. The user answers through the web client.
        if not choices:
            if multi_question:
                notice = "_(此问需多题作答，请在 cubebox 网页端继续。)_"
            elif multi_select:
                notice = "_(多选题需在 cubebox 网页端作答。)_"
            else:
                notice = "_(此问题需要文本输入；请在 cubebox 网页端继续。)_"
            prompt = f"{prompt}\n\n{notice}" if prompt else notice
        state.card_state.pending_input = PendingInput(
            kind="ask_user",
            run_id=state.run_id,
            question=prompt,
            choices=choices,
            question_id=question_id,
            answer_key=answer_key,
        )
        state.last_patch_monotonic = now
        return OutboundOp(kind="patch_card") if state.card_id else OutboundOp(kind="card_create")

    if etype == "sandbox_confirm_request":
        from cubebox.im.card_model import PendingInput

        question_id = str(data.get("question_id") or "")
        command = str(data.get("command") or "")
        prompt = "是否允许执行以下命令？"
        if command:
            prompt = f"{prompt}\n\n```bash\n{command}\n```"
        state.card_state.pending_input = PendingInput(
            kind="sandbox_confirm",
            run_id=state.run_id,
            question=prompt,
            choices=[("允许", "approve", "primary"), ("拒绝", "deny", "danger")],
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
        # RunManager stamps ``data.paused=true`` on the DoneEvent when the
        # final_status is ``paused_hitl`` (cubebox/streams/run_manager.py).
        # That's a soft pause, not a terminal end — resume_run_with_answer
        # appends more events to the same run_id stream after the user
        # answers. If we treat it as terminal here the tailer exits and the
        # resumed events fall on the floor; the user sees the card stuck on
        # the pending question with no follow-up answer ever delivered.
        # Render a patch (so any pending_input change lands) and keep going.
        if bool(data.get("paused")):
            if state.card_id is None:
                return OutboundOp(kind="card_create")
            state.last_patch_monotonic = now
            return OutboundOp(kind="patch_card")
        state.card_state.finalized = True
        elapsed_ms = max(0, int((now - state.card_state.run_start_monotonic) * 1000))
        state.card_state.elapsed_ms = elapsed_ms
        return OutboundOp(kind="finalize", final=True)

    if etype == "error":
        state.card_state.finalized = True
        state.card_state.error = str(data.get("message") or "the run failed")
        return OutboundOp(kind="finalize", final=True)

    return None


async def register_awaiting_responder(
    *,
    run_id: str,
    responder_open_id: str,
    redis_key_prefix: str,
    set_fn: Callable[..., Awaitable[None]],
    ttl_seconds: int = _AWAITING_TTL_DEFAULT_SECONDS,
) -> None:
    """Bind which Feishu user is allowed to answer this run's AskUser /
    SandboxConfirm card.

    Called by the tailer when it sees an ``ask_user_request`` /
    ``sandbox_confirm_request`` event. The webhook ingress reads the
    same key (``{redis_key_prefix}:run:{run_id}:awaiting_responder``)
    to gate the callback — both sides MUST use the same prefix so two
    cubebox envs sharing one Redis don't collide.

    ``ttl_seconds`` lets the caller honor the event's ``timeout_seconds``
    field — answering 20 minutes into a 30-minute HITL window would
    otherwise hit a dropped binding and surface "这不是发给你的". Clamped
    to ``[1, _AWAITING_TTL_MAX_SECONDS]`` so a malformed event can't pin
    a Redis key beyond a day or set ex=0.

    No-ops when ``run_id`` or ``responder_open_id`` is empty (defensive —
    a missing responder_open_id should not blank out a prior valid
    binding). ``redis_key_prefix`` defaults are NOT permitted: a missing
    prefix would collide silently across envs.
    """
    if not run_id or not responder_open_id:
        return
    ttl = max(1, min(int(ttl_seconds or _AWAITING_TTL_DEFAULT_SECONDS), _AWAITING_TTL_MAX_SECONDS))
    await set_fn(
        f"{redis_key_prefix}:run:{run_id}:awaiting_responder",
        responder_open_id,
        ex=ttl,
    )


def note_flood_strike(state: RenderState) -> None:
    """Tailer-side hook: connector signaled a flood-control response.

    After ``_MAX_FLOOD_STRIKES`` consecutive strikes we permanently disable
    progressive patches — the final ``done`` / ``error`` op still emits one
    terminal patch so the user sees a complete answer even on a hot
    rate-limit run.
    """
    state.consecutive_flood_strikes += 1
    if state.consecutive_flood_strikes >= _MAX_FLOOD_STRIKES:
        state.edits_disabled = True


def note_edit_success(state: RenderState) -> None:
    """Tailer-side hook: a streaming edit succeeded — reset the strike counter."""
    state.consecutive_flood_strikes = 0


class OutboundRunTailer:
    """Tail a run's Redis event stream and emit ops via the connector.

    Lifecycle calls go through the connector's ``on_processing_start /
    _complete / _failed`` hooks — platform-specific rendering lives in
    the injected ``OpDispatcher``, not here.

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
        dispatcher: Any | None = None,
        artifact_dispatcher: Any | None = None,
        responder_open_id: str | None = None,
        block_ms: int = 2000,
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._run_id = run_id
        self._connector = connector
        self._state = state
        self._dispatcher = dispatcher
        self._artifact_dispatcher = artifact_dispatcher
        self._responder_open_id = responder_open_id
        self._block_ms = block_ms

    async def maybe_register_awaiting_responder(self, *, ev_payload: dict[str, Any]) -> None:
        """Register the awaiting_responder binding if the event is a pending input.

        Called by the run loop AFTER fold_event has emitted the patch_card op
        and after the dispatcher has run it. Idempotent — safe to call on
        every event; only writes Redis when the event is the right shape.

        The TTL is derived from the event's ``timeout_seconds`` (falling back
        to the default when absent or non-positive) so a 30-minute HITL pause
        doesn't outlive its responder binding and surface "这不是发给你的"
        on a still-valid answer.
        """
        etype = ev_payload.get("type")
        if etype not in ("ask_user_request", "sandbox_confirm_request"):
            return
        if not self._responder_open_id:
            return

        async def _set(key: str, value: str, *, ex: int) -> None:
            if self._redis is None:
                return
            await self._redis.set(key, value, ex=ex)

        data = ev_payload.get("data") or {}
        timeout_raw = data.get("timeout_seconds")
        try:
            ttl_seconds = int(timeout_raw) if timeout_raw is not None else 0
        except (TypeError, ValueError):
            ttl_seconds = 0
        if ttl_seconds <= 0:
            ttl_seconds = _AWAITING_TTL_DEFAULT_SECONDS

        # Cap at the run-event TTL: the resume path resolves the conversation
        # via the Redis ``RunMeta`` hash (set by RunManager with this TTL).
        # A binding that outlives RunMeta surfaces "会话已结束" on a click that
        # the responder gate would have accepted — confusing and worse UX
        # than just refusing the click promptly.
        from cubebox.config import config as _cfg

        run_event_ttl = int(_cfg.get("streaming.run_event_ttl_seconds", 43200))
        if run_event_ttl > 0:
            ttl_seconds = min(ttl_seconds, run_event_ttl)

        await register_awaiting_responder(
            run_id=self._run_id,
            responder_open_id=self._responder_open_id,
            redis_key_prefix=self._prefix,
            set_fn=_set,
            ttl_seconds=ttl_seconds,
        )

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
                    if (
                        ev.payload.get("type") == "artifact"
                        and self._artifact_dispatcher is not None
                    ):
                        artifact_payload = (ev.payload.get("data") or {}).get("artifact") or {}
                        try:
                            await self._artifact_dispatcher.handle(artifact_payload)
                        except Exception:
                            logger.warning("artifact dispatch failed", exc_info=True)
                    if op is None:
                        continue
                    delivered = await self._dispatch_op(op, is_terminal=op.final)
                    try:
                        await self.maybe_register_awaiting_responder(ev_payload=ev.payload)
                    except Exception:
                        logger.warning(
                            "[outbound] register_awaiting_responder raised", exc_info=True
                        )
                    if op.final:
                        done = True
                        # Mark succeeded only when the terminal op landed
                        # AND the run wasn't an error. Otherwise the
                        # reaction lifecycle would clear ⏳ via
                        # ``on_processing_complete`` (no ❌), making a
                        # failed run indistinguishable from a healthy one.
                        if delivered and self._state.card_state.error is None:
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
            # Release the dispatcher's platform resources. Idempotent and
            # safe even when dispatcher is a test fake.
            if self._dispatcher is not None:
                try:
                    await self._dispatcher.aclose()
                except Exception:
                    logger.warning("[outbound] dispatcher.aclose() raised", exc_info=True)

    async def _dispatch_op(self, op: OutboundOp, *, is_terminal: bool) -> bool:
        """Delegate one OutboundOp to the injected OpDispatcher.

        Returns True iff the op was delivered. When no dispatcher is
        injected (``dispatcher=None``), every op short-circuits to False.
        """
        _ = is_terminal
        if self._dispatcher is None:
            return False
        state = self._state
        if op.kind == "card_create":
            return bool(await self._dispatcher.dispatch_create(state))
        if op.kind == "stream_text":
            return bool(await self._dispatcher.dispatch_stream(state, op.text))
        if op.kind == "patch_card":
            return bool(await self._dispatcher.dispatch_patch(state))
        if op.kind == "finalize":
            return bool(await self._dispatcher.dispatch_finalize(state))
        return False


def find_split_point(text: str, limit: int) -> int:
    """Find a line-boundary split point at or before ``limit``."""
    idx = text.rfind("\n", 0, limit)
    if idx > limit // 2:
        return idx + 1
    return limit


class _FloodSignal(Exception):
    """Marker the connector raises to tell the tailer to back off edits.

    Connectors translate platform-specific rate-limit responses into this
    typed exception so ``OutboundRunTailer`` can apply adaptive backoff
    without knowing each platform's quota codes.
    """
