"""Handle Adaptive Card Action.Submit callbacks from Teams."""

from __future__ import annotations

from typing import Any

from loguru import logger


async def handle_card_action(
    *,
    data: dict[str, Any],
    operator_aad_id: str,
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

    from cubebox.cache import get_redis
    from cubebox.im.resume import resolve_full_question_id, resume_paused_run

    redis = get_redis()

    # Replay guard — Azure Bot Service may retry invokes on timeout.
    replay_key = f"{redis_key_prefix}:teams:invoke:{run_id}:{short_qid}:{akey}"
    fresh = await redis.set(replay_key, "1", ex=1800, nx=True)
    if not fresh:
        return True

    # Responder check — only the user who triggered the HITL pause may answer.
    expected_raw = await redis.get(f"{redis_key_prefix}:run:{run_id}:awaiting_responder")
    if expected_raw is not None:
        expected = (
            expected_raw.decode()
            if isinstance(expected_raw, (bytes, bytearray))
            else str(expected_raw)
        )
        if expected and operator_aad_id and expected != operator_aad_id:
            logger.info(
                "[Teams] card action rejected: expected={} got={}",
                expected,
                operator_aad_id,
            )
            return False

    try:
        question_id = await resolve_full_question_id(run_id, short_qid)
    except Exception:
        logger.opt(exception=True).warning("[Teams] question_id resolution failed")
        return False

    return await resume_paused_run(
        run_id=run_id,
        input_kind=kind,
        choice=value,
        operator_open_id=operator_aad_id,
        question_id=question_id,
        answer_key=akey,
        run_manager=run_manager,
    )
