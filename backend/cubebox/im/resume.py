"""Resume-paused-run wrapper for IM card-action callbacks.

Wraps cubebox's existing ``run_manager.resume_run_with_answer`` with the
extra plumbing IM needs: resolve conversation/org/workspace from a bare
``run_id`` (the SSE / web path always has these in the request context;
IM only has the click payload), build the right answer shape per
``input_kind``, and translate cubepi resume exceptions into a True/False
outcome so the ingress can pick a user-visible toast.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


async def _resolve_run_context(run_id: str) -> tuple[str, str, str, str] | None:
    """Look up ``(conversation_id, user_id, org_id, workspace_id)`` for a run.

    Two-step lookup:

    1. Read the Redis ``RunMeta`` hash to map ``run_id → conversation_id``.
       ``RunManager`` writes this on every run start; TTL is the run-event
       TTL (12h by default) which more than covers a paused HITL window.
    2. Load the ``Conversation`` row by id (unscoped — we don't yet know
       which workspace it lives in) to read ``creator_user_id``,
       ``workspace_id``, ``org_id``.

    Returns None when either step fails. The caller treats None as
    "session ended" and surfaces a toast.
    """
    try:
        import os

        from sqlalchemy import select

        from cubebox.cache import get_redis
        from cubebox.config import config as _cfg
        from cubebox.db.engine import async_session_maker
        from cubebox.models.conversation import Conversation
        from cubebox.streams.run_events import get_run_meta

        redis = get_redis()
        base_prefix = _cfg.get("redis.key_prefix", "cubebox")
        env_name = os.getenv("ENV_FOR_DYNACONF", "development")
        prefix = f"{base_prefix}:{env_name}"

        meta = await get_run_meta(redis, prefix=prefix, run_id=run_id)
        if meta is None:
            return None
        conversation_id = meta.conversation_id

        async with async_session_maker() as session:
            row = (
                await session.execute(
                    select(Conversation).where(
                        Conversation.id == conversation_id,  # type: ignore[arg-type]
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return (
            str(row.id),
            str(row.creator_user_id),
            str(row.org_id),
            str(row.workspace_id),
        )
    except Exception:
        logger.warning("[resume] _resolve_run_context failed for {}", run_id, exc_info=True)
        return None


async def resume_paused_run(
    *,
    run_id: str,
    input_kind: str,
    choice: str,
    operator_open_id: str,
    question_id: str = "",
    run_manager: Any,
    **_: Any,
) -> bool:
    """Forward a human input from an IM card-action click to the paused run.

    Returns True if the run accepted the input; False if the run is not
    pending, the resume call failed, or the conversation context could
    not be resolved. Never raises — the caller (IM ingress) maps False
    onto a friendly toast.
    """
    resolved = await _resolve_run_context(run_id)
    if resolved is None:
        logger.warning("[resume] cannot resolve run_id={}", run_id)
        return False
    conversation_id, user_id, org_id, workspace_id = resolved

    answer: Any
    if input_kind == "sandbox_confirm":
        from typing import Literal, cast

        from cubepi.hitl.types import ApproveAnswer

        decision: Literal["approve", "deny"] = "approve" if choice == "approve" else "deny"
        answer = ApproveAnswer(
            decision=cast(Literal["approve", "deny", "edit"], decision),
            reason=f"via Feishu card (operator={operator_open_id})",
        )
    elif input_kind == "ask_user":
        # v1 simplification: cubepi's ask_user form expects a dict keyed
        # by the question's ``key``. We don't carry the form schema in
        # the card payload yet, so pass the choice under a generic
        # ``"choice"`` key — single-question prompts accept this shape.
        answer = {"choice": choice}
    else:
        logger.warning("[resume] unknown input_kind={}", input_kind)
        return False

    from cubebox.streams.run_manager import (
        ResumeConflict,
        ResumeInFlight,
        ResumeNoPending,
        ResumeStaleAnswer,
        RunContext,
    )

    ctx = RunContext(user_id=user_id, org_id=org_id, workspace_id=workspace_id)

    try:
        await run_manager.resume_run_with_answer(
            conversation_id=conversation_id,
            run_id=run_id,
            question_id=question_id,
            answer=answer,
            ctx=ctx,
        )
        return True
    except (ResumeNoPending, ResumeStaleAnswer, ResumeInFlight, ResumeConflict) as exc:
        logger.warning("[resume] resume_run_with_answer rejected: {}", exc)
        return False
    except Exception:
        logger.warning(
            "[resume] resume_run_with_answer raised unexpectedly for run_id={}",
            run_id,
            exc_info=True,
        )
        return False


__all__ = ["resume_paused_run"]
