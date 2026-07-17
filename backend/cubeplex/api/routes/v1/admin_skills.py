"""Admin-only skill marketplace endpoints. Gated by require_org_admin.

See spec § 5.1.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.skill import (
    InstallRequest,
    PatchInstallRequest,
    SkillContentResponse,
    SkillDetail,
    SkillFiles,
    SkillSummary,
    SkillVersionDetail,
    WorkspaceBindingsRequest,
)
from cubeplex.api.schemas.skill_discovery import (
    AdminInstallCandidateRequest,
    CandidatePreviewResponse,
    InstallCandidateResponse,
    SkillCandidateResponse,
)
from cubeplex.auth.dependencies import require_org_admin, resolve_current_org_id
from cubeplex.config import config as _config
from cubeplex.db import get_session
from cubeplex.models import Skill, User
from cubeplex.repositories.organization import OrganizationRepository
from cubeplex.repositories.skill import (
    OrgPreinstalledTombstoneRepository,
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
    WorkspaceSkillBindingRepository,
)
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.discovery import SkillDiscoveryService, SkillInstallError, SkillInstallService
from cubeplex.skills.frontmatter import (
    InvalidFrontmatterError,
    extract_env_vars,
    parse_skill_md,
    peek_skill_name,
)
from cubeplex.skills.service import (
    FileTooLargeError,
    InvalidSkillNameError,
    SkillCatalogService,
    SkillMdMissingError,
    SkillPublishService,
    VersionCollisionError,
)
from cubeplex.skills.sources.base import CandidateIdError, decode_candidate_id
from cubeplex.skills.sources.registry import SkillsAdapterManager
from cubeplex.utils.time import utc_isoformat

router = APIRouter(prefix="/admin/skills", tags=["admin-skills"])


def _cache() -> SkillCache:
    cache_root = Path(_config.get("skills.cache_root", "skills_cache"))
    return SkillCache(cache_root=cache_root)


def _env_vars_from_skill_md(content: str) -> list[str]:
    try:
        fm = parse_skill_md(content, default_version="0.0.0")
    except Exception:
        return []
    return extract_env_vars(fm.raw_metadata)


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


@router.get("/discover", response_model=list[SkillCandidateResponse])
async def admin_discover_skills(
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=20),
) -> list[SkillCandidateResponse]:
    org_id = await resolve_current_org_id(user, session)
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=org_id,
        org_slug=org.slug,
        workspace_id=None,
        include_local=False,  # skip local catalog: prevents slug collision dropping remote candidates
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
        if c.source_kind == "remote"  # admin discover: only external candidates
    ]


@router.get("/discover/preview", response_model=CandidatePreviewResponse)
async def admin_preview_candidate(
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    candidate_id: str = Query(...),
) -> CandidatePreviewResponse:
    org_id = await resolve_current_org_id(user, session)
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    try:
        kind, source_id, source_ref = decode_candidate_id(candidate_id)
    except CandidateIdError as e:
        raise HTTPException(status_code=400, detail="BAD_CANDIDATE_ID") from e
    if kind != "remote":
        raise HTTPException(status_code=400, detail="REMOTE_CANDIDATES_ONLY")
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=org_id,
        org_slug=org.slug,
        workspace_id=None,
        include_local=False,
    )
    remote = registry.adapter_by_id(source_id)
    if remote is None:
        raise HTTPException(status_code=404, detail="REGISTRY_NOT_FOUND")
    try:
        files = await remote.fetch(source_ref)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"FETCH_FAILED: {e}") from e
    if "SKILL.md" not in files:
        raise HTTPException(status_code=404, detail="SKILL_MD_MISSING")
    skill_md = files["SKILL.md"].decode("utf-8", errors="replace")
    name = peek_skill_name(skill_md) or source_ref.rsplit("/", 1)[-1]
    return CandidatePreviewResponse(
        candidate_id=candidate_id,
        name=name,
        canonical_name=name,
        content=skill_md,
        env_vars=_env_vars_from_skill_md(skill_md),
    )


@router.post("/install-candidate", response_model=InstallCandidateResponse)
async def admin_install_candidate(
    body: AdminInstallCandidateRequest,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InstallCandidateResponse:
    org_id = await resolve_current_org_id(user, session)
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="ORG_NOT_FOUND")
    catalog = SkillCatalogService(session=session, cache=_cache())
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=org_id,
        org_slug=org.slug,
        workspace_id=None,
        include_local=False,
    )
    publisher = SkillPublishService(session=session, cache=_cache())
    install_svc = SkillInstallService(
        session=session,
        registry=registry,
        publisher=publisher,
        org_id=org_id,
        org_slug=org.slug,
        workspace_id=None,
        actor_user_id=user.id,
    )
    try:
        result = await install_svc.install(body.candidate_id)
    except SkillInstallError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return InstallCandidateResponse(
        canonical_name=result.canonical_name,
        skill_id=result.skill_id,
        installed_version=result.installed_version,
    )


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
                created_at=utc_isoformat(v.created_at),
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
        files=[
            SkillFiles(
                rel_path=p,
                size=len(b),
                content_hash=hashlib.md5(b, usedforsecurity=False).hexdigest(),
            )
            for p, b in files_list
        ],
    )


@router.get("/{skill_id}/versions/{version}/files/{path:path}")
async def get_skill_version_file(
    skill_id: str,
    version: str,
    path: str,
    *,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> bytes:
    from fastapi.responses import Response

    org_id = await resolve_current_org_id(user, session)
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    sv = await SkillVersionRepository(session).find(skill_id, version)
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

    # Preinstalled skills default auto_bind=True; uploaded default False.
    # On upgrade, auto_bind=None preserves the user's existing setting.
    existing_install = await OrgSkillInstallRepository(session).get(org_id, skill_id)
    default_auto_bind: bool | None = None
    if existing_install is None:
        default_auto_bind = skill.source == "preinstalled"

    install = await OrgSkillInstallRepository(session).upsert(
        org_id=org_id,
        skill_id=skill_id,
        installed_version=body.version,
        installed_by_user_id=user.id,
        auto_bind=default_auto_bind,
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
    from sqlalchemy import delete

    from cubeplex.models import WorkspaceSkillBinding

    org_id = await resolve_current_org_id(user, session)
    skill = await SkillRepository(session).get(skill_id)
    if skill is None or not _visible(skill, org_id):
        raise HTTPException(status_code=404, detail="SKILL_NOT_FOUND")
    install = await OrgSkillInstallRepository(session).get(org_id, skill_id)
    if install is not None:
        # Use explicit DELETE + flush to guarantee bindings are removed before
        # the install row, otherwise the FK from workspace_skill_bindings to
        # org_skill_installs blocks the install delete. Filtering by
        # install_id alone (without org_id) is safe because the install_id is
        # globally unique and each binding row inherently belongs to the same
        # org as the install it references.
        await session.execute(
            delete(WorkspaceSkillBinding).where(
                WorkspaceSkillBinding.org_skill_install_id == install.id  # type: ignore[arg-type]
            )
        )
        await session.flush()
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
