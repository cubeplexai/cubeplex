"""Workspace settings routes: agent config, skill bindings, MCP bindings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from cubebox.api.schemas.ws_settings import AgentConfigOut, AgentConfigPatch
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.models.agent_config import AgentConfig

router = APIRouter(prefix="/ws/{workspace_id}/settings", tags=["workspace-settings"])


async def _get_or_create_agent_config(
    session: AsyncSession, org_id: str, workspace_id: str
) -> AgentConfig:
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


@router.get("/agent", response_model=AgentConfigOut)
async def get_agent_config(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentConfigOut:
    cfg = await _get_or_create_agent_config(session, ctx.org_id, ctx.workspace_id)
    return AgentConfigOut(system_prompt=cfg.system_prompt)


@router.put("/agent", response_model=AgentConfigOut)
async def update_agent_config(
    body: AgentConfigPatch,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentConfigOut:
    cfg = await _get_or_create_agent_config(session, ctx.org_id, ctx.workspace_id)
    cfg.system_prompt = body.system_prompt
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return AgentConfigOut(system_prompt=cfg.system_prompt)
