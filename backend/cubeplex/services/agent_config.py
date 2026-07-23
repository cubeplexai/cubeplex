"""Shared AgentConfig (workspace persona) service.

Used by ``GET/PUT /settings/agent`` and by ``persona_get`` / ``persona_update``
tools so validation and get-or-create stay in one place.
"""

from __future__ import annotations

import hashlib

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from cubeplex.models.agent_config import AgentConfig

PERSONA_MAX_LENGTH = 8000


class PersonaTooLongError(ValueError):
    """Raised when system_prompt exceeds PERSONA_MAX_LENGTH."""


class PersonaConflictError(ValueError):
    """Raised when optimistic concurrency fingerprint does not match."""


def persona_fingerprint(text: str) -> str:
    """Short stable fingerprint of persona text for optimistic concurrency."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def get_or_create_agent_config(
    session: AsyncSession, org_id: str, workspace_id: str
) -> AgentConfig:
    """Load workspace AgentConfig, creating an empty row if missing."""
    result = await session.execute(
        select(AgentConfig).where(
            AgentConfig.org_id == org_id,
            AgentConfig.workspace_id == workspace_id,
        )
    )
    cfg = result.scalar_one_or_none()
    if cfg is not None:
        return cfg
    try:
        cfg = AgentConfig(org_id=org_id, workspace_id=workspace_id)
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
        return cfg
    except IntegrityError:
        await session.rollback()
        result = await session.execute(
            select(AgentConfig).where(
                AgentConfig.org_id == org_id,
                AgentConfig.workspace_id == workspace_id,
            )
        )
        return result.scalar_one()


async def get_system_prompt(session: AsyncSession, org_id: str, workspace_id: str) -> str:
    """Return current workspace persona text (empty string if none)."""
    cfg = await get_or_create_agent_config(session, org_id, workspace_id)
    return cfg.system_prompt or ""


async def set_system_prompt(
    session: AsyncSession,
    org_id: str,
    workspace_id: str,
    text: str,
    *,
    expected_fingerprint: str | None = None,
) -> AgentConfig:
    """Replace workspace persona text.

    ``expected_fingerprint`` — when set, the row is locked with
    ``SELECT … FOR UPDATE`` and the write commits only if the current prompt
    still hashes to this value (optimistic concurrency for HITL overwrite,
    safe against concurrent Settings/agent writers).
    """
    if len(text) > PERSONA_MAX_LENGTH:
        raise PersonaTooLongError(
            f"persona exceeds max length {PERSONA_MAX_LENGTH} (got {len(text)})"
        )

    if expected_fingerprint is not None:
        # Ensure the row exists first (may commit once on create).
        await get_or_create_agent_config(session, org_id, workspace_id)
        result = await session.execute(
            select(AgentConfig)
            .where(
                AgentConfig.org_id == org_id,
                AgentConfig.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        cfg = result.scalar_one()
        current = cfg.system_prompt or ""
        if persona_fingerprint(current) != expected_fingerprint:
            raise PersonaConflictError(
                "persona changed since confirmation started; re-read and try again"
            )
    else:
        cfg = await get_or_create_agent_config(session, org_id, workspace_id)

    cfg.system_prompt = text
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return cfg
