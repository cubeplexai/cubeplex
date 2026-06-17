"""Handle Slack Block Kit action interactions (button clicks)."""

from __future__ import annotations

from typing import Any

from loguru import logger


async def _resolve_full_question_id(run_id: str, short_qid: str) -> str:
    """Map truncated question_id back to the full value from DB pending."""
    try:
        from cubebox.im.resume import _resolve_run_context

        resolved = await _resolve_run_context(run_id)
        if resolved is None:
            return short_qid
        conversation_id = resolved[0]

        from cubebox.agents.checkpointer import init_checkpointer

        async with init_checkpointer() as cp:
            pending = await cp.load_pending_request(conversation_id)
        if pending is not None and pending.question_id.startswith(short_qid):
            return pending.question_id
    except Exception:
        logger.warning("[Slack] _resolve_full_question_id failed", exc_info=True)
    return short_qid


async def handle_block_action(
    *,
    action: dict[str, Any],
    body: dict[str, Any],
    run_manager: Any,
    redis_key_prefix: str,
) -> None:
    """Route a Block Kit button click to the resume path.

    Button action_id format: ``im:{kind}:{run_id}:{short_qid}:{akey}:{value}``
    """
    action_id: str = action.get("action_id", "")
    if not action_id.startswith("im:"):
        return

    parts = action_id.split(":", 5)
    if len(parts) < 6:
        return

    _, kind, run_id, short_qid, answer_key, value = parts
    question_id = await _resolve_full_question_id(run_id, short_qid)

    from cubebox.im.resume import resume_paused_run

    try:
        await resume_paused_run(
            run_id=run_id,
            input_kind=kind,
            choice=value,
            operator_open_id="",
            question_id=question_id,
            answer_key=answer_key,
            run_manager=run_manager,
        )
    except Exception:
        logger.warning("[Slack] block action handler failed", exc_info=True)
