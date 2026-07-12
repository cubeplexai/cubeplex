"""Member-callable skill endpoints under /api/v1/ws/{workspace_id}/skills.

See spec § 5.1.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.skill import (
    PublishFromArtifactRequest,
    SkillContentResponse,
    SkillFiles,
    SkillSummary,
    SkillVersionDetail,
)
from cubeplex.api.schemas.skill_discovery import (
    CandidatePreviewResponse,
    InstallCandidateRequest,
    InstallCandidateResponse,
    SkillCandidateResponse,
    SkillRefreshResponse,
)
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.config import config as _config
from cubeplex.db import get_session
from cubeplex.repositories.organization import OrganizationRepository
from cubeplex.repositories.skill import (
    OrgPreinstalledTombstoneRepository,
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
)
from cubeplex.repositories.skill_registry import SkillRegistryRepository
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.discovery import (
    SkillDiscoveryService,
    SkillInstallError,
    SkillInstallService,
)
from cubeplex.skills.frontmatter import InvalidFrontmatterError, extract_env_vars, parse_skill_md
from cubeplex.skills.service import (
    FileTooLargeError,
    InvalidSkillNameError,
    InvalidZipPathError,
    SkillCatalogService,
    SkillMdMissingError,
    SkillPublishService,
    VersionCollisionError,
)
from cubeplex.skills.sources.base import CandidateIdError, decode_candidate_id
from cubeplex.skills.sources.registry import SkillsAdapterManager
from cubeplex.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/skills", tags=["ws-skills"])


def _cache() -> SkillCache:
    return SkillCache(cache_root=Path(_config.get("skills.cache_root", "skills_cache")))


def _env_vars_from_skill_md(content: str) -> list[str]:
    try:
        fm = parse_skill_md(content, default_version="0.0.0")
    except Exception:
        return []
    return extract_env_vars(fm.raw_metadata)


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
    # Build registry id→name map once for the org (used to annotate SkillSummary)
    registry_rows = await SkillRegistryRepository(session).list_for_org(ctx.org_id)
    registry_names: dict[str, str] = {r.id: r.name for r in registry_rows}
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
                imported_from_registry_id=s.imported_from_registry_id,
                imported_from_registry_name=registry_names.get(s.imported_from_registry_id)
                if s.imported_from_registry_id
                else None,
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
                imported_from_registry_id=s.imported_from_registry_id,
                imported_from_registry_name=registry_names.get(s.imported_from_registry_id)
                if s.imported_from_registry_id
                else None,
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
                imported_from_registry_id=s.imported_from_registry_id,
                imported_from_registry_name=registry_names.get(s.imported_from_registry_id)
                if s.imported_from_registry_id
                else None,
            )
            for s in skills
            if (q is None or q.lower() in s.name.lower() or q.lower() in s.description.lower())
            and (tag is None or tag in s.keywords)
        ]


@router.get("/discover", response_model=list[SkillCandidateResponse])
async def discover_skills(
    workspace_id: str,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=20),
) -> list[SkillCandidateResponse]:
    org = await OrganizationRepository(session).get(ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=ctx.org_id,
        org_slug=org.slug,
        workspace_id=workspace_id,
    )
    cands = await SkillDiscoveryService(registry).discover(q, limit=limit)
    return [
        SkillCandidateResponse(
            candidate_id=c.candidate_id,
            name=c.name,
            canonical_name=c.canonical_name,
            description=c.description,
            source_kind=c.source_kind,
            keywords=c.keywords,
            version=c.version,
            trust=c.trust.value,
            install_state=c.install_state,
            stars=c.stars,
            install_count=c.install_count,
            source_name=c.source_name,
            repo=c.repo,
            unvetted=(c.source_kind == "remote" and c.trust.value != "official"),
        )
        for c in cands
    ]


@router.get("/discover/preview", response_model=CandidatePreviewResponse)
async def preview_candidate(
    workspace_id: str,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    candidate_id: str = Query(...),
) -> CandidatePreviewResponse:
    org = await OrganizationRepository(session).get(ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    try:
        kind, source_id, source_ref = decode_candidate_id(candidate_id)
    except CandidateIdError as e:
        raise HTTPException(status_code=400, detail="BAD_CANDIDATE_ID") from e
    catalog = SkillCatalogService(session=session, cache=_cache())
    if kind == "local":
        skill = await SkillRepository(session).get(source_ref)
        if skill is None or not _visible(skill, ctx.org_id):
            raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
        # Tombstoned preinstalled skills are hidden in discover + refused on install;
        # preview must match or it leaks SKILL.md after an admin uninstall.
        if skill.source == "preinstalled":
            tombstone = await OrgPreinstalledTombstoneRepository(session).get(ctx.org_id, skill.id)
            if tombstone is not None:
                raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
        sv = await SkillVersionRepository(session).find(skill.id, skill.current_version)
        if sv is None:
            raise HTTPException(status_code=404, detail="SKILL_VERSION_NOT_FOUND")
        content = await catalog.fetch_skill_md(sv.id)
        return CandidatePreviewResponse(
            candidate_id=candidate_id,
            name=skill.name,
            canonical_name=skill.name,
            content=content,
            env_vars=_env_vars_from_skill_md(content),
        )
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=ctx.org_id,
        org_slug=org.slug,
        workspace_id=workspace_id,
    )
    remote = registry.adapter_by_id(source_id)
    if remote is None:
        raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    try:
        files = await remote.fetch(source_ref)
        if "SKILL.md" not in files:
            raise HTTPException(status_code=404, detail="SKILL_MD_MISSING")
        content = files["SKILL.md"].decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail="INVALID_SKILL") from e
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(
            "preview_candidate remote fetch failed: source_ref={!r} error={}", source_ref, e
        )
        raise HTTPException(status_code=502, detail="REMOTE_FETCH_FAILED") from e
    slug = source_ref.rsplit("/", 1)[-1]
    return CandidatePreviewResponse(
        candidate_id=candidate_id,
        name=slug,
        canonical_name=f"{org.slug}:{slug}",
        content=content,
        env_vars=_env_vars_from_skill_md(content),
    )


@router.post("/install", status_code=201, response_model=InstallCandidateResponse)
async def install_candidate(
    workspace_id: str,
    body: InstallCandidateRequest,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InstallCandidateResponse:
    org = await OrganizationRepository(session).get(ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=ctx.org_id,
        org_slug=org.slug,
        workspace_id=workspace_id,
    )
    install = SkillInstallService(
        session=session,
        registry=registry,
        publisher=SkillPublishService(session=session, cache=_cache()),
        org_id=ctx.org_id,
        org_slug=org.slug,
        workspace_id=workspace_id,
        actor_user_id=ctx.user.id,
    )
    try:
        result = await install.install(body.candidate_id)
    except CandidateIdError as e:
        raise HTTPException(status_code=400, detail="BAD_CANDIDATE_ID") from e
    except InvalidZipPathError as e:
        raise HTTPException(
            status_code=400, detail={"code": "INVALID_PATH", "reason": str(e)}
        ) from e
    except FileTooLargeError as e:
        raise HTTPException(
            status_code=400, detail={"code": "FILE_TOO_LARGE", "reason": str(e)}
        ) from e
    except VersionCollisionError as e:
        raise HTTPException(
            status_code=409, detail={"code": "VERSION_EXISTS", "reason": str(e)}
        ) from e
    except (InvalidFrontmatterError, InvalidSkillNameError, SkillMdMissingError) as e:
        raise HTTPException(
            status_code=400, detail={"code": "INVALID_SKILL", "reason": str(e)}
        ) from e
    except SkillInstallError as e:
        raise HTTPException(
            status_code=400, detail={"code": "INSTALL_FAILED", "reason": str(e)}
        ) from e
    return InstallCandidateResponse(
        canonical_name=result.canonical_name,
        skill_id=result.skill_id,
        installed_version=result.installed_version,
    )


@router.post("/{skill_id}/refresh", response_model=SkillRefreshResponse)
async def refresh_skill(
    workspace_id: str,
    skill_id: str,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillRefreshResponse:
    """Re-import a skill from its original remote registry.

    For skills installed from a registry (imported_from_registry_id is set),
    re-fetches the SKILL.md from the stored source_ref and publishes a new
    version if the content has changed. For preinstalled/manually uploaded
    skills, always returns changed=False.
    """
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, ctx.org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")

    if not skill.imported_from_registry_id or not skill.imported_from_source_ref:
        return SkillRefreshResponse(
            canonical_name=skill.name,
            skill_id=skill.id,
            installed_version=skill.current_version,
            changed=False,
        )

    org = await OrganizationRepository(session).get(ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")

    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=ctx.org_id,
        org_slug=org.slug,
        workspace_id=workspace_id,
    )
    publisher = SkillPublishService(session=session, cache=_cache())
    install_svc = SkillInstallService(
        session=session,
        registry=registry,
        publisher=publisher,
        org_id=ctx.org_id,
        org_slug=org.slug,
        workspace_id=workspace_id,
        actor_user_id=ctx.user.id,
    )
    prev_version = skill.current_version
    try:
        result = await install_svc._install_remote(
            skill.imported_from_registry_id,
            skill.imported_from_source_ref,
        )
    except SkillInstallError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return SkillRefreshResponse(
        canonical_name=result.canonical_name,
        skill_id=result.skill_id,
        installed_version=result.installed_version,
        changed=result.installed_version != prev_version,
    )


@router.get("/{skill_id}/versions", response_model=list[SkillVersionDetail])
async def list_skill_versions(
    workspace_id: str,
    skill_id: str,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SkillVersionDetail]:
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, ctx.org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    versions = await SkillVersionRepository(session).list_for_skill(skill_id)
    return [
        SkillVersionDetail(
            id=v.id,
            version=v.version,
            description=v.description or "",
            keywords=v.keywords or [],
            storage_prefix=v.storage_prefix,
            entry_file=v.entry_file,
            uploaded_by_user_id=v.uploaded_by_user_id,
            created_at=utc_isoformat(v.created_at),
        )
        for v in versions
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
        files=[
            SkillFiles(
                rel_path=p,
                size=len(b),
                content_hash=hashlib.md5(b, usedforsecurity=False).hexdigest(),
            )
            for p, b in files_list
        ],
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
    from fastapi.responses import Response

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
    target = (cache_dir / path).resolve()
    if not target.is_relative_to(cache_dir.resolve()):
        raise HTTPException(status_code=400, detail="INVALID_PATH")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")
    data = target.read_bytes()
    try:
        text = data.decode("utf-8")
        return Response(content=text, media_type="text/plain; charset=utf-8")  # type: ignore[return-value]
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="BINARY_FILE") from None


@router.post("/publish", status_code=201)
async def publish_from_ws(
    workspace_id: str,
    request: Request,
    *,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Member publish: multipart .zip OR JSON {artifact_id}."""
    org = await OrganizationRepository(session).get(ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    publisher = SkillPublishService(session=session, cache=_cache())
    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("application/json"):
            body = await request.json()
            req = PublishFromArtifactRequest(**body)
            sv = await publisher.publish_from_artifact(
                org_id=ctx.org_id,
                org_slug=org.slug,
                actor_user_id=ctx.user.id,
                artifact_id=req.artifact_id,
                workspace_id=workspace_id,
            )
        else:
            form = await request.form()
            file = form.get("file")
            if file is None or not hasattr(file, "read"):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "MISSING_BODY",
                        "reason": "expected multipart file= or JSON {artifact_id}",
                    },
                )
            zip_bytes = await file.read()
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
