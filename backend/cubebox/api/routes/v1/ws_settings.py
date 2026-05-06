"""Workspace settings routes: agent config, skill bindings, MCP bindings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from cubebox.api.schemas.ws_settings import (
    AgentConfigOut,
    AgentConfigPatch,
    SkillBindingPatch,
    SkillInstallCreate,
    SkillInstallOut,
    WorkspaceSkillsOut,
)
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.models.agent_config import AgentConfig
from cubebox.repositories.skill import (
    OrgSkillInstallRepository,
    SkillVersionRepository,
    WorkspaceSkillBindingRepository,
)

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


@router.get("/skills", response_model=WorkspaceSkillsOut)
async def list_workspace_skills(
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspaceSkillsOut:
    install_repo = OrgSkillInstallRepository(session)
    binding_repo = WorkspaceSkillBindingRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )

    org_installs = await install_repo.list_for_org(ctx.org_id)
    ws_private = await install_repo.list_for_workspace_private(ctx.org_id, ctx.workspace_id)

    org_skills = []
    for install in org_installs:
        binding = await binding_repo.get_by_install(install.id)
        enabled = binding.enabled if binding is not None else install.auto_bind
        org_skills.append(
            SkillInstallOut(
                install_id=install.id,
                skill_id=install.skill_id,
                installed_version=install.installed_version,
                enabled=enabled,
                scope="org",
            )
        )

    workspace_skills = [
        SkillInstallOut(
            install_id=i.id,
            skill_id=i.skill_id,
            installed_version=i.installed_version,
            enabled=True,
            scope="workspace",
        )
        for i in ws_private
    ]

    return WorkspaceSkillsOut(org_skills=org_skills, workspace_skills=workspace_skills)


@router.patch("/skills/{install_id}")
async def toggle_skill_binding(
    install_id: str,
    body: SkillBindingPatch,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    install_repo = OrgSkillInstallRepository(session)
    install = await install_repo.get_by_id(install_id)
    if install is None or install.org_id != ctx.org_id or install.workspace_id is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="org skill install not found")

    binding_repo = WorkspaceSkillBindingRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    if body.enabled:
        binding = await binding_repo.enable(install_id)
        enabled = binding.enabled
    else:
        await binding_repo.disable(install_id)
        enabled = False

    return {"install_id": install_id, "enabled": enabled}


@router.post("/skills", status_code=status.HTTP_201_CREATED)
async def install_workspace_skill(
    body: SkillInstallCreate,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    from cubebox.repositories.skill import SkillRepository

    skill_repo = SkillRepository(session)
    skill = await skill_repo.get(body.skill_id)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="skill not found in catalog")
    if skill.source != "preinstalled" and skill.owner_org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="skill not found in catalog")

    version_repo = SkillVersionRepository(session)
    skill_version = await version_repo.find(body.skill_id, body.version)
    if skill_version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="skill version not found")

    install_repo = OrgSkillInstallRepository(session)
    install = await install_repo.create_for_workspace(
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        skill_id=body.skill_id,
        installed_version=body.version,
        installed_by_user_id=ctx.user.id,
    )
    return {"install_id": install.id, "skill_id": install.skill_id, "scope": "workspace"}


@router.delete("/skills/{install_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_skill(
    install_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    install_repo = OrgSkillInstallRepository(session)
    deleted = await install_repo.delete_workspace_private(
        install_id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workspace skill install not found")
