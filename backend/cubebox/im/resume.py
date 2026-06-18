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


async def _resolve_run_context(
    run_id: str,
) -> tuple[str, str, str, str, str | None, bool] | None:
    """Look up ``(conversation_id, user_id, org_id, workspace_id, topic_id, is_group_chat)`` for a run.

    Two-step lookup:

    1. Read the Redis ``RunMeta`` hash to map ``run_id → conversation_id``.
       ``RunManager`` writes this on every run start; TTL is the run-event
       TTL (12h by default) which more than covers a paused HITL window.
    2. Load the ``Conversation`` row by id (unscoped — we don't yet know
       which workspace it lives in) to read ``creator_user_id``,
       ``workspace_id``, ``org_id``, ``topic_id``.

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
            str(row.topic_id) if row.topic_id is not None else None,
            bool(row.is_group_chat),
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
    answer_key: str = "",
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
    conversation_id, user_id, org_id, workspace_id, topic_id, is_group_chat = resolved
    if topic_id is not None or is_group_chat:
        logger.warning(
            "[resume] refusing IM resume for topic / group-chat conversation {} "
            "(run_id={}) — topic-aware IM resume not implemented (v1 scope)",
            conversation_id,
            run_id,
        )
        return False

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
        # cubepi ask_user expects a dict keyed by the question's `key`
        # (the form schema). The renderer plumbs questions[0].key through
        # the button payload → ActionPayload → ResumeAction → here. If
        # the question carried no key (defensive fallback for malformed
        # payloads or single-key prompts), drop in "choice" so cubepi
        # gets a syntactically valid dict; a schema mismatch is then
        # cubepi's to report, not ours.
        key = answer_key or "choice"
        answer = {key: choice}
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

    ctx = RunContext(
        user_id=user_id,
        org_id=org_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )

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


async def resolve_full_question_id(run_id: str, short_qid: str) -> str:
    """Map truncated question_id back to the full value from DB pending.

    The IM action_id / custom_id only carries the first 8 chars of the
    question_id to stay within platform limits.  ``resume_paused_run``
    does an exact match, so we need the full hash.
    """
    try:
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
        logger.warning("[IM] resolve_full_question_id failed", exc_info=True)
    return short_qid


__all__ = ["resume_paused_run", "resolve_full_question_id"]
