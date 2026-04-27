"""Admin-only skill marketplace endpoints. Gated by require_org_admin.

See spec § 5.1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.skill import (
    InstallRequest,
    PatchInstallRequest,
    SkillContentResponse,
    SkillDetail,
    SkillFiles,
    SkillSummary,
    SkillVersionDetail,
    WorkspaceBindingsRequest,
)
from cubebox.auth.dependencies import require_org_admin, resolve_current_org_id
from cubebox.config import config as _config
from cubebox.db import get_session
from cubebox.models import Skill, User
from cubebox.repositories.organization import OrganizationRepository
from cubebox.repositories.skill import (
    OrgPreinstalledTombstoneRepository,
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
    WorkspaceSkillBindingRepository,
)
from cubebox.skills.cache import SkillCache
from cubebox.skills.frontmatter import InvalidFrontmatterError
from cubebox.skills.service import (
    FileTooLargeError,
    InvalidSkillNameError,
    SkillCatalogService,
    SkillMdMissingError,
    SkillPublishService,
    VersionCollisionError,
)

router = APIRouter(prefix="/admin/skills", tags=["admin-skills"])


def _cache() -> SkillCache:
    cache_root = Path(_config.get("skills.cache_root", "skills_cache"))
    return SkillCache(cache_root=cache_root)


@router.get("", response_model=list[SkillSummary])
async def list_skills(
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    source: str | None = Query(None),
    installed: bool | None = Query(None),
    q: str | None = Query(None),
    tag: str | None = Query(None),
) -> list[SkillSummary]:
    org_id = await resolve_current_org_id(user, session)
    skills = await SkillRepository(session).list_visible_for_org(org_id, source=source)
    installs_repo = OrgSkillInstallRepository(session)

    summaries: list[SkillSummary] = []
    for s in skills:
        if q and q.lower() not in s.name.lower() and q.lower() not in s.description.lower():
            continue
        if tag and tag not in s.keywords:
            continue
        install = await installs_repo.get(org_id, s.id)
        if install is None:
            install_state = "uninstalled"
            installed_version: str | None = None
        elif install.installed_version != s.current_version:
            install_state = "update_available"
            installed_version = install.installed_version
        else:
            install_state = "installed"
            installed_version = install.installed_version

        if installed is True and install is None:
            continue
        if installed is False and install is not None:
            continue

        summaries.append(
            SkillSummary(
                id=s.id,
                name=s.name,
                source=s.source,  # type: ignore[arg-type]
                description=s.description,
                current_version=s.current_version,
                keywords=s.keywords,
                install_state=install_state,  # type: ignore[arg-type]
                installed_version=installed_version,
                workspace_bindings_count=0,
            )
        )
    return summaries


@router.get("/{skill_id}", response_model=SkillDetail)
async def get_skill(
    skill_id: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillDetail:
    org_id = await resolve_current_org_id(user, session)
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")

    versions = await SkillVersionRepository(session).list_for_skill(skill_id)
    install = await OrgSkillInstallRepository(session).get(org_id, skill_id)
    if install is None:
        install_state = "uninstalled"
        installed_version = None
    elif install.installed_version != skill.current_version:
        install_state = "update_available"
        installed_version = install.installed_version
    else:
        install_state = "installed"
        installed_version = install.installed_version

    return SkillDetail(
        id=skill.id,
        name=skill.name,
        source=skill.source,  # type: ignore[arg-type]
        description=skill.description,
        current_version=skill.current_version,
        keywords=skill.keywords,
        versions=[
            SkillVersionDetail(
                id=v.id,
                version=v.version,
                description=v.description,
                keywords=v.keywords,
                storage_prefix=v.storage_prefix,
                entry_file=v.entry_file,
                uploaded_by_user_id=v.uploaded_by_user_id,
                created_at=v.created_at,
            )
            for v in versions
        ],
        install_state=install_state,  # type: ignore[arg-type]
        installed_version=installed_version,
        auto_bind=install.auto_bind if install is not None else None,
    )


@router.get("/{skill_id}/versions/{version}", response_model=SkillContentResponse)
async def get_skill_version(
    skill_id: str,
    version: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillContentResponse:
    org_id = await resolve_current_org_id(user, session)
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    sv = await SkillVersionRepository(session).find(skill_id, version)
    if sv is None:
        raise HTTPException(status_code=404, detail="SKILL_VERSION_NOT_FOUND")

    catalog = SkillCatalogService(session=session, cache=_cache())
    content = await catalog.fetch_skill_md(sv.id)
    files_list = await catalog.list_files_for_sandbox_sync(sv.id, storage_prefix=sv.storage_prefix)
    return SkillContentResponse(
        skill_id=skill.id,
        skill_version_id=sv.id,
        name=skill.name,
        version=sv.version,
        content=content,
        files=[SkillFiles(rel_path=p, size=len(b)) for p, b in files_list],
    )


@router.post("/{skill_id}/install", status_code=200)
async def install_skill(
    skill_id: str,
    body: InstallRequest,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    org_id = await resolve_current_org_id(user, session)
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    sv = await SkillVersionRepository(session).find(skill_id, body.version)
    if sv is None:
        raise HTTPException(status_code=404, detail="SKILL_VERSION_NOT_FOUND")

    await OrgPreinstalledTombstoneRepository(session).remove_tombstone(org_id, skill_id)

    install = await OrgSkillInstallRepository(session).upsert(
        org_id=org_id,
        skill_id=skill_id,
        installed_version=body.version,
        installed_by_user_id=user.id,
    )
    return {"install_id": install.id, "installed_version": install.installed_version}


@router.patch("/{skill_id}/install", status_code=200)
async def patch_install(
    skill_id: str,
    body: PatchInstallRequest,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    org_id = await resolve_current_org_id(user, session)
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    install = await OrgSkillInstallRepository(session).get(org_id, skill_id)
    if install is None:
        raise HTTPException(status_code=404, detail="SKILL_NOT_INSTALLED")
    install.auto_bind = body.auto_bind
    session.add(install)
    await session.commit()
    return {"auto_bind": install.auto_bind}


@router.delete("/{skill_id}/install", status_code=204)
async def uninstall_skill(
    skill_id: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    from sqlalchemy import select

    from cubebox.models import WorkspaceSkillBinding

    org_id = await resolve_current_org_id(user, session)
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    install = await OrgSkillInstallRepository(session).get(org_id, skill_id)
    if install is not None:
        result = await session.execute(
            select(WorkspaceSkillBinding).where(
                WorkspaceSkillBinding.org_id == org_id,  # type: ignore[arg-type]
                WorkspaceSkillBinding.org_skill_install_id == install.id,  # type: ignore[arg-type]
            )
        )
        for binding in result.scalars().all():
            await session.delete(binding)
        await session.delete(install)
        await session.commit()

    if skill.source == "preinstalled":
        await OrgPreinstalledTombstoneRepository(session).add_tombstone(
            org_id=org_id, skill_id=skill_id, hidden_by_user_id=user.id
        )


@router.post("/upload", status_code=201)
async def upload_skill(
    file: Annotated[UploadFile, File(...)],
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    org_id = await resolve_current_org_id(user, session)
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    zip_bytes = await file.read()
    publisher = SkillPublishService(session=session, cache=_cache())
    try:
        sv = await publisher.publish_from_zip(
            org_id=org_id,
            org_slug=org.slug,
            actor_user_id=user.id,
            zip_bytes=zip_bytes,
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


# --- Workspace bindings (admin-managed) ----------------------------------


bindings_router = APIRouter(
    prefix="/admin/workspaces/{ws_id}/skills", tags=["admin-skill-bindings"]
)


@bindings_router.get("", response_model=list[SkillSummary])
async def list_workspace_skills(
    ws_id: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SkillSummary]:
    """List all org-installed skills with their effective binding state for this workspace."""
    org_id = await resolve_current_org_id(user, session)
    installs = await OrgSkillInstallRepository(session).list_for_org(org_id)
    if not installs:
        return []
    skill_repo = SkillRepository(session)
    bindings_repo = WorkspaceSkillBindingRepository(session, org_id=org_id, workspace_id=ws_id)
    all_bindings = await bindings_repo.list_all()
    binding_by_install: dict[str, bool | None] = {
        b.org_skill_install_id: b.enabled for b in all_bindings
    }
    out: list[SkillSummary] = []
    for install_obj in installs:
        skill = await skill_repo.get(install_obj.skill_id)
        if skill is None:
            continue
        explicit = binding_by_install.get(install_obj.id)  # True / False / None
        if explicit is True:
            ws_state: str = "enabled"
        elif explicit is False:
            ws_state = "disabled"
        elif install_obj.auto_bind:
            ws_state = "auto"
        else:
            ws_state = "disabled"
        out.append(
            SkillSummary(
                id=skill.id,
                name=skill.name,
                source=skill.source,  # type: ignore[arg-type]
                description=skill.description,
                current_version=skill.current_version,
                keywords=skill.keywords,
                install_state="installed",
                installed_version=install_obj.installed_version,
                workspace_bindings_count=1 if ws_state in ("enabled", "auto") else 0,
                workspace_binding_state=ws_state,  # type: ignore[arg-type]
            )
        )
    return sorted(out, key=lambda s: s.name)


@bindings_router.post("", status_code=200)
async def enable_skills_in_workspace(
    ws_id: str,
    body: WorkspaceBindingsRequest,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    org_id = await resolve_current_org_id(user, session)
    install_repo = OrgSkillInstallRepository(session)
    bindings = WorkspaceSkillBindingRepository(session, org_id=org_id, workspace_id=ws_id)
    enabled_count = 0
    for skill_id in body.skill_ids:
        install = await install_repo.get(org_id, skill_id)
        if install is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "SKILL_NOT_INSTALLED", "skill_id": skill_id},
            )
        await bindings.enable(install.id)
        enabled_count += 1
    return {"enabled": enabled_count}


@bindings_router.delete("/{skill_id}", status_code=204)
async def disable_skill_in_workspace(
    ws_id: str,
    skill_id: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    org_id = await resolve_current_org_id(user, session)
    install = await OrgSkillInstallRepository(session).get(org_id, skill_id)
    if install is None:
        return
    bindings = WorkspaceSkillBindingRepository(session, org_id=org_id, workspace_id=ws_id)
    await bindings.disable(install.id)


def _visible(skill: Skill, org_id: str) -> bool:
    return skill.source == "preinstalled" or skill.owner_org_id == org_id
