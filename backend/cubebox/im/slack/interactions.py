"""Handle Slack Block Kit action interactions (button clicks)."""

from __future__ import annotations

from typing import Any

from loguru import logger


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

    from cubebox.im.resume import resolve_full_question_id

    question_id = await resolve_full_question_id(run_id, short_qid)

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
        logger.opt(exception=True).warning("[Slack] block action handler failed")
