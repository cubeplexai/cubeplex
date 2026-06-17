# backend/cubebox/im/discord/interactions.py
"""Handle Discord component interactions (button clicks for AskUser / SandboxConfirm)."""

from __future__ import annotations

from typing import Any

import discord
from loguru import logger


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

    from cubebox.im.resume import resolve_full_question_id

    question_id = await resolve_full_question_id(run_id, short_qid)

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
