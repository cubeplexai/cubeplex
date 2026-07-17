"""Workspace settings routes: agent config + skill bindings.

MCP connector management lives on the dedicated four-layer surface under
``/ws/{workspace_id}/mcp/{templates,connectors,installs,...}`` — see
``routes/v1/ws_mcp.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from cubeplex.api.schemas.ws_settings import (
    AgentConfigOut,
    AgentConfigPatch,
    SkillBindingPatch,
    SkillInstallCreate,
    SkillInstallOut,
    WorkspaceSkillsOut,
)
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.config import config as _config
from cubeplex.db import get_session
from cubeplex.models.agent_config import AgentConfig
from cubeplex.repositories.organization import OrganizationRepository
from cubeplex.repositories.skill import (
    OrgSkillInstallRepository,
    SkillVersionRepository,
    WorkspaceSkillBindingRepository,
)
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.frontmatter import InvalidFrontmatterError
from cubeplex.skills.service import (
    FileTooLargeError,
    InvalidSkillNameError,
    SkillMdMissingError,
    SkillPublishService,
    VersionCollisionError,
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

    org_rows = await install_repo.list_org_wide_with_bindings(ctx.org_id, ctx.workspace_id)
    org_skills = [
        SkillInstallOut(
            install_id=install.id,
            skill_id=install.skill_id,
            name=skill.name,
            description=skill.description,
            installed_version=install.installed_version,
            enabled=binding.enabled if binding is not None else install.auto_bind,
            scope="org",
        )
        for install, binding, skill in org_rows
    ]

    ws_rows = await install_repo.list_workspace_private_with_skill(ctx.org_id, ctx.workspace_id)
    workspace_skills = [
        SkillInstallOut(
            install_id=install.id,
            skill_id=install.skill_id,
            name=skill.name,
            description=skill.description,
            installed_version=install.installed_version,
            enabled=True,
            scope="workspace",
        )
        for install, skill in ws_rows
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
    from cubeplex.repositories.skill import SkillRepository

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


def _skills_cache() -> SkillCache:
    return SkillCache(cache_root=Path(_config.get("skills.cache_root", "skills_cache")))


@router.post("/skills/upload", status_code=status.HTTP_201_CREATED)
async def upload_workspace_skill(
    file: Annotated[UploadFile, File(...)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Upload a skill zip and install it as workspace-private.

    The Skill / SkillVersion rows still land in the org catalog (so future
    workspaces could install the same skill if their members choose to), but
    the install row is workspace-private — this workspace is the only one
    seeing it through `/settings/skills`.
    """
    org = await OrganizationRepository(session).get(ctx.org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="ORG_NOT_FOUND")
    zip_bytes = await file.read()
    publisher = SkillPublishService(session=session, cache=_skills_cache())
    try:
        sv = await publisher.publish_from_zip(
            org_id=ctx.org_id,
            org_slug=org.slug,
            actor_user_id=ctx.user.id,
            zip_bytes=zip_bytes,
            workspace_id=ctx.workspace_id,
        )
    except InvalidFrontmatterError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_FRONTMATTER", "field": e.field, "reason": e.reason},
        ) from e
    except InvalidSkillNameError as e:
        raise HTTPException(
            status_code=400, detail={"code": "INVALID_SKILL_NAME", "reason": str(e)}
        ) from e
    except SkillMdMissingError as e:
        raise HTTPException(
            status_code=400, detail={"code": "SKILL_MD_MISSING", "reason": str(e)}
        ) from e
    except FileTooLargeError as e:
        raise HTTPException(
            status_code=400, detail={"code": "FILE_TOO_LARGE", "reason": str(e)}
        ) from e
    except VersionCollisionError as e:
        raise HTTPException(
            status_code=409, detail={"code": "VERSION_EXISTS", "reason": str(e)}
        ) from e
    return {"skill_version_id": sv.id, "skill_id": sv.skill_id, "version": sv.version}
