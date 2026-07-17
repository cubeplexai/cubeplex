"""Handle DingTalk interactive card action callbacks (button clicks)."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from cubeplex.im.resume import resolve_full_question_id, resume_paused_run


async def handle_card_action(
    *,
    callback: dict[str, Any],
    run_manager: Any,
) -> None:
    """Route a DingTalk card button click to the resume path.

    Button action_id format: ``im:{kind}:{run_id}:{short_qid}:{akey}:{value}``
    """
    content_raw = callback.get("content", "{}")
    try:
        content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
    except (json.JSONDecodeError, TypeError):
        return

    action_id: str = content.get("cardActionId", "")
    if not action_id:
        private_data = content.get("cardPrivateData", {})
        params = private_data.get("params", {}) if isinstance(private_data, dict) else {}
        action_id = params.get("cardActionId", "")
    if not action_id.startswith("im:"):
        return

    parts = action_id.split(":", 5)
    if len(parts) < 6:
        return

    _, kind, run_id, short_qid, answer_key, value = parts

    operator_open_id: str = callback.get("userId", "")

    question_id = await resolve_full_question_id(run_id, short_qid)

    try:
        await resume_paused_run(
            run_id=run_id,
            input_kind=kind,
            choice=value,
            operator_open_id=operator_open_id,
            question_id=question_id,
            answer_key=answer_key,
            run_manager=run_manager,
        )
    except Exception:
        logger.opt(exception=True).warning("[DingTalk] card action handler failed")
