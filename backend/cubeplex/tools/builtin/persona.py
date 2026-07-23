"""Workspace persona tools as cubepi.AgentTool instances.

``persona_get`` / ``persona_update`` read and write ``AgentConfig.system_prompt``
— the same field Settings → Agent Persona edits. Overwriting a non-empty
persona requires HITL confirmation via the run-level CheckpointedChannel.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.hitl.binding import HitlBinding
from cubepi.hitl.channel import CheckpointedChannel, HitlChannel
from cubepi.hitl.exceptions import HitlCancelled, HitlTimedOut
from cubepi.hitl.types import Option, Question
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubeplex.db.engine import async_session_maker
from cubeplex.services.agent_config import (
    PERSONA_MAX_LENGTH,
    PersonaConflictError,
    PersonaTooLongError,
    get_system_prompt,
    persona_fingerprint,
    set_system_prompt,
)

_PREVIEW_CHARS = 280


class PersonaGetArgs(BaseModel):
    """No arguments — returns the current workspace persona."""


class PersonaUpdateArgs(BaseModel):
    system_prompt: str = Field(
        max_length=PERSONA_MAX_LENGTH,
        description=(
            "Full replacement text for the workspace Agent Persona "
            f"(max {PERSONA_MAX_LENGTH} characters). Call persona_get first "
            "when applying an incremental change so you can compose the full "
            "new document."
        ),
    )
    reason: str = Field(
        default="",
        max_length=500,
        description="Short reason shown to the user in the confirmation card.",
    )


def _preview(text: str, limit: int = _PREVIEW_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text=json.dumps(payload, ensure_ascii=False))],
        is_error=is_error,
    )


def create_persona_tools(
    *,
    org_id: str,
    workspace_id: str,
    channel: HitlChannel | None = None,
    include_update: bool = True,
) -> list[AgentTool]:  # type: ignore[type-arg]
    """Build persona_get and optionally persona_update tools.

    ``include_update`` should be True only for interactive member-originated
    chat runs. Scheduled / IM / automation tool lists omit the write tool.
    ``channel`` is required when ``include_update`` is True (HITL overwrite).
    """

    async def _persona_get_execute(
        tool_call_id: str,
        args: PersonaGetArgs,
        *,
        signal: asyncio.Event | None = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, args, signal, on_update
        async with async_session_maker() as session:
            text = await get_system_prompt(session, org_id, workspace_id)
        return _tool_result(
            {
                "system_prompt": text,
                "length": len(text),
                "max_length": PERSONA_MAX_LENGTH,
                "fingerprint": persona_fingerprint(text),
            }
        )

    persona_get = AgentTool(
        name="persona_get",
        description=(
            "Read the current workspace Agent Persona (Settings → Agent Persona / "
            "system instructions). Returns the full text, length, and max length. "
            "This is workspace-wide standing instructions for every conversation "
            "in this workspace — not personal memory items."
        ),
        parameters=PersonaGetArgs,
        execute=_persona_get_execute,
    )

    tools: list[AgentTool] = [persona_get]  # type: ignore[type-arg]

    if not include_update:
        return tools

    if channel is None:
        raise ValueError("persona_update requires a HITL channel")

    async def _persona_update_execute(
        tool_call_id: str,
        args: PersonaUpdateArgs,
        *,
        signal: asyncio.Event | None = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, on_update
        new_text = args.system_prompt
        if len(new_text) > PERSONA_MAX_LENGTH:
            return _tool_result(
                {
                    "updated": False,
                    "error": (
                        f"persona exceeds max length {PERSONA_MAX_LENGTH} (got {len(new_text)})"
                    ),
                },
                is_error=True,
            )

        async with async_session_maker() as session:
            current = await get_system_prompt(session, org_id, workspace_id)

        if current == new_text:
            return _tool_result(
                {
                    "updated": False,
                    "reason": "unchanged",
                    "length": len(current),
                    "max_length": PERSONA_MAX_LENGTH,
                }
            )

        previous_length = len(current)
        previous_hash = persona_fingerprint(current)
        needs_confirm = bool(current.strip())

        if needs_confirm:
            reason_line = f"\nReason: {args.reason}" if args.reason.strip() else ""
            prompt = (
                "Update the workspace Agent Persona for ALL members of this "
                "workspace?\n\n"
                f"Current length: {previous_length} characters\n"
                f"New length: {len(new_text)} characters\n"
                f"New text preview:\n{_preview(new_text)}"
                f"{reason_line}\n\n"
                "This replaces Settings → Agent Persona (system instructions) "
                "and applies on subsequent turns."
            )
            questions = [
                Question(
                    key="confirm",
                    prompt=prompt,
                    options=[
                        Option(
                            label="Approve update",
                            value="yes",
                            description="Replace the workspace persona with the new text",
                        ),
                        Option(
                            label="Cancel",
                            value="no",
                            description="Keep the current persona unchanged",
                        ),
                    ],
                )
            ]
            try:
                answers = await channel.ask(questions, signal=signal)
            except HitlCancelled as exc:
                return _tool_result(
                    {
                        "updated": False,
                        "reason": "cancelled",
                        "error": str(exc.reason),
                    }
                )
            except HitlTimedOut as exc:
                return _tool_result(
                    {
                        "updated": False,
                        "reason": "timed_out",
                        "error": f"timed out after {exc.seconds} seconds",
                    }
                )

            decision = answers.get("confirm", "")
            if isinstance(decision, list):
                decision = decision[0] if decision else ""
            if str(decision).lower() not in {"yes", "approve", "approved", "true"}:
                return _tool_result(
                    {
                        "updated": False,
                        "reason": "denied",
                        "previous_length": previous_length,
                    }
                )

        try:
            async with async_session_maker() as session:
                cfg = await set_system_prompt(
                    session,
                    org_id,
                    workspace_id,
                    new_text,
                    expected_fingerprint=previous_hash if needs_confirm else None,
                )
        except PersonaTooLongError as exc:
            return _tool_result(
                {"updated": False, "error": str(exc)},
                is_error=True,
            )
        except PersonaConflictError as exc:
            return _tool_result(
                {
                    "updated": False,
                    "reason": "conflict",
                    "error": str(exc),
                },
                is_error=True,
            )

        return _tool_result(
            {
                "updated": True,
                "length": len(cfg.system_prompt or ""),
                "previous_length": previous_length,
                "max_length": PERSONA_MAX_LENGTH,
                "workspace_wide": True,
                "message": (
                    "Updated workspace Agent Persona. Applies on the next turn "
                    "for all members of this workspace."
                ),
            }
        )

    checkpointed = isinstance(channel, CheckpointedChannel)
    hitl_binding = HitlBinding(
        checkpointed=checkpointed,
        run_id=getattr(channel, "_run_id", None) if checkpointed else None,
    )
    persona_update = AgentTool(
        name="persona_update",
        description=(
            "Replace the workspace Agent Persona (Settings → Agent Persona — "
            "standing system instructions for every conversation in this "
            "workspace). Full document replace only. Affects ALL members. "
            "Prefer memory_save for small personal preferences; use this when "
            "the user asks to change persona / system instructions / 人设 / "
            "workspace-wide standing behavior. Call persona_get first for "
            "incremental edits. Overwriting a non-empty persona requires user "
            "confirmation. Never put secrets in the persona. Max "
            f"{PERSONA_MAX_LENGTH} characters."
        ),
        parameters=PersonaUpdateArgs,
        execute=_persona_update_execute,
        execution_mode="sequential",
        hitl=hitl_binding,
    )
    # Built-in HITL tool: do not set the custom-tool durability guard so
    # CheckpointedChannel.ask works from inside this tool body.
    persona_update.hitl_builtin = True
    tools.append(persona_update)
    return tools
