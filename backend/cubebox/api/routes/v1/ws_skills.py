"""Member-callable skill endpoints under /api/v1/ws/{workspace_id}/skills.

See spec § 5.1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.skill import (
    SkillContentResponse,
    SkillFiles,
    SkillSummary,
)
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.config import config as _config
from cubebox.db import get_session
from cubebox.repositories.organization import OrganizationRepository
from cubebox.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
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

router = APIRouter(prefix="/ws/{workspace_id}/skills", tags=["ws-skills"])


def _cache() -> SkillCache:
    return SkillCache(cache_root=Path(_config.get("skills.cache_root", "skills_cache")))


@router.get("", response_model=list[SkillSummary])
async def list_skills_in_ws(
    workspace_id: str,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    scope: Literal["workspace", "org", "catalog"] = Query("workspace"),
    source: str | None = Query(None),
    q: str | None = Query(None),
    tag: str | None = Query(None),
) -> list[SkillSummary]:
    repo = SkillRepository(session)
    if scope == "workspace":
        catalog = SkillCatalogService(session=session, cache=_cache())
        resolved = await catalog.list_enabled_for_workspace(workspace_id, org_id=ctx.org_id)
        skill_ids = [r.skill_id for r in resolved]
        maybe_skills = [await repo.get(sid) for sid in skill_ids]
        ws_skills = [s for s in maybe_skills if s is not None]
        if q:
            ws_skills = [
                s
                for s in ws_skills
                if q.lower() in s.name.lower() or q.lower() in s.description.lower()
            ]
        if tag:
            ws_skills = [s for s in ws_skills if tag in s.keywords]
        return [
            SkillSummary(
                id=s.id,
                name=s.name,
                source=s.source,  # type: ignore[arg-type]
                description=s.description,
                current_version=s.current_version,
                keywords=s.keywords,
                install_state="installed",
                installed_version=None,
                workspace_bindings_count=1,
            )
            for s in ws_skills
        ]
    elif scope == "org":
        skills = await repo.list_visible_for_org(ctx.org_id, source=source)
        installs = await OrgSkillInstallRepository(session).list_for_org(ctx.org_id)
        installed_versions: dict[str, str] = {i.skill_id: i.installed_version for i in installs}
        return [
            SkillSummary(
                id=s.id,
                name=s.name,
                source=s.source,  # type: ignore[arg-type]
                description=s.description,
                current_version=s.current_version,
                keywords=s.keywords,
                install_state="installed",
                installed_version=installed_versions.get(s.id),
                workspace_bindings_count=0,
            )
            for s in skills
            if (q is None or q.lower() in s.name.lower() or q.lower() in s.description.lower())
            and (tag is None or tag in s.keywords)
            and s.id in installed_versions
        ]
    else:  # catalog
        skills = await repo.list_visible_for_org(ctx.org_id, source=source)
        return [
            SkillSummary(
                id=s.id,
                name=s.name,
                source=s.source,  # type: ignore[arg-type]
                description=s.description,
                current_version=s.current_version,
                keywords=s.keywords,
                install_state="uninstalled",
                workspace_bindings_count=0,
            )
            for s in skills
            if (q is None or q.lower() in s.name.lower() or q.lower() in s.description.lower())
            and (tag is None or tag in s.keywords)
        ]


@router.get("/{skill_id}", response_model=SkillContentResponse)
async def preview_skill(
    workspace_id: str,
    skill_id: str,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    version: str | None = Query(None),
) -> SkillContentResponse:
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, ctx.org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")

    target_version = version
    if target_version is None:
        install = await OrgSkillInstallRepository(session).get(ctx.org_id, skill_id)
        target_version = install.installed_version if install else skill.current_version

    sv = await SkillVersionRepository(session).find(skill_id, target_version)
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


@router.get("/{skill_id}/files/{path:path}")
async def get_skill_file(
    workspace_id: str,
    skill_id: str,
    path: str,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    version: str | None = Query(None),
) -> bytes:
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, ctx.org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    target_version = version
    if target_version is None:
        install = await OrgSkillInstallRepository(session).get(ctx.org_id, skill_id)
        target_version = install.installed_version if install else skill.current_version
    sv = await SkillVersionRepository(session).find(skill_id, target_version)
    if sv is None:
        raise HTTPException(status_code=404, detail="SKILL_VERSION_NOT_FOUND")

    cache_dir = await _cache().ensure_extracted(sv.id, storage_prefix=sv.storage_prefix)
    target = cache_dir / path
    if not target.is_file():
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")
    return target.read_bytes()


@router.post("/publish", status_code=201)
async def publish_from_ws(
    workspace_id: str,
    file: Annotated[UploadFile | None, File()] = None,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Member publish: multipart .zip OR JSON {artifact_id} (Batch 2)."""
    if file is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MISSING_BODY",
                "reason": "expected multipart file or {artifact_id}",
            },
        )
    org = await OrganizationRepository(session).get(ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    zip_bytes = await file.read()
    publisher = SkillPublishService(session=session, cache=_cache())
    try:
        sv = await publisher.publish_from_zip(
            org_id=ctx.org_id,
            org_slug=org.slug,
            actor_user_id=ctx.user.id,
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


def _visible(skill: object, org_id: str) -> bool:
    return (
        getattr(skill, "source", None) == "preinstalled"
        or getattr(skill, "owner_org_id", None) == org_id
    )
