# backend/cubebox/im/discord/interactions.py
"""Handle Discord component interactions (button clicks for AskUser / SandboxConfirm)."""

from __future__ import annotations

from typing import Any

import discord
from loguru import logger


async def _resolve_full_question_id(run_id: str, short_qid: str) -> str:
    """Map truncated question_id back to the full value from DB pending.

    The Discord custom_id only carries the first 8 chars of the question_id
    to stay within the 100-char limit.  ``resume_run_with_answer`` does an
    exact match, so we need the full hash.
    """
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
        logger.warning("[Discord] _resolve_full_question_id failed", exc_info=True)
    return short_qid


async def handle_component_interaction(
    interaction: discord.Interaction,
    *,
    run_manager: Any,
    redis_key_prefix: str,
) -> None:
    """Route a button click to the resume path.

    Button custom_id format: ``im:{kind}:{run_id}:{short_qid}:{akey}:{value}``
    where kind is ``ask_user`` or ``sandbox_confirm``.
    ``short_qid`` is a truncated question_id (first 8 chars) — the full
    value is loaded from the DB pending so the resume path sees an exact
    match.
    """
    raw_data: Any = interaction.data
    custom_id: str = raw_data.get("custom_id", "") if raw_data else ""
    if not custom_id.startswith("im:"):
        return

    parts = custom_id.split(":", 5)
    if len(parts) < 6:
        await interaction.response.send_message("Invalid button.", ephemeral=True)
        return

    _, kind, run_id, short_qid, answer_key, value = parts

    # The custom_id carries a truncated question_id (8 chars) to stay
    # within Discord's 100-char limit.  Load the full question_id from
    # the DB pending so resume_run_with_answer sees an exact match.
    question_id = await _resolve_full_question_id(run_id, short_qid)

    from cubebox.im.resume import resume_paused_run

    try:
        result = await resume_paused_run(
            run_id=run_id,
            input_kind=kind,
            choice=value,
            operator_open_id="",
            question_id=question_id,
            answer_key=answer_key,
            run_manager=run_manager,
        )
        if result:
            await interaction.response.send_message("✅", ephemeral=True)
        else:
            await interaction.response.send_message("操作已过期或已被处理。", ephemeral=True)
    except Exception:
        logger.warning("[Discord] interaction handler failed", exc_info=True)
        try:
            await interaction.response.send_message("处理失败。", ephemeral=True)
        except Exception:
            pass
