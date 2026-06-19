"""Handle Adaptive Card Action.Submit callbacks from Teams."""

from __future__ import annotations

from typing import Any

from loguru import logger


async def handle_card_action(
    *,
    data: dict[str, Any],
    run_manager: Any,
    redis_key_prefix: str,
) -> bool:
    """Parse Adaptive Card submit data and resume the paused run.

    ``data`` is the ``value`` dict from the card action, expected to
    contain an ``action`` key with the format
    ``im:{kind}:{run_id}:{short_qid}:{akey}:{value}``.

    Returns True on success, False on failure.
    """
    action_str = str(data.get("action") or "")
    if not action_str.startswith("im:"):
        return False
    parts = action_str.split(":", maxsplit=5)
    if len(parts) < 6:
        logger.warning("[Teams] malformed action_id: {}", action_str)
        return False
    _, kind, run_id, short_qid, akey, value = parts

    from cubebox.im.resume import resolve_full_question_id, resume_paused_run

    try:
        question_id = await resolve_full_question_id(run_id, short_qid)
    except Exception:
        logger.warning("[Teams] question_id resolution failed", exc_info=True)
        return False

    return await resume_paused_run(
        run_id=run_id,
        input_kind=kind,
        choice=value,
        operator_open_id="",
        question_id=question_id,
        answer_key=akey,
        run_manager=run_manager,
    )
