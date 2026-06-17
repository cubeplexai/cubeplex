"""Admin-only provider and model endpoints. Gated by require_org_admin."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.provider import (
    ModelCreate,
    ModelOut,
    ModelReadinessOut,
    ModelTest,
    ModelUpdate,
    OrgProviderOverrideOut,
    OrgProviderOverrideUpdate,
    ProviderCreate,
    ProviderLivenessRequest,
    ProviderOut,
    ProviderTestRequest,
    ProviderTestStreamRequest,
    ProviderUpdate,
)
from cubebox.auth.dependencies import require_org_admin, resolve_current_org_id
from cubebox.credentials.dependencies import build_credential_service
from cubebox.db import get_session
from cubebox.llm.readiness import capability_fingerprint, derive_readiness
from cubebox.models import User
from cubebox.models.org_provider_override import OrgProviderOverride
from cubebox.models.provider import Model, Provider
from cubebox.repositories.model import ModelRepository
from cubebox.repositories.org_provider_override import OrgProviderOverrideRepository
from cubebox.repositories.provider import ProviderRepository
from cubebox.services.provider_probe import ProbeResult, ProbeStep
from cubebox.services.provider_service import (
    InvalidProviderSlugError,
    ModelNotFoundError,
    ProviderNameConflictError,
    ProviderNotFoundError,
    ProviderOAuthNotImplementedError,
    ProviderOverrideNotApplicableError,
    ProviderService,
    ProviderSlugConflictError,
    ProviderSystemReadonlyError,
)
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/admin", tags=["admin-providers"])


async def _svc(user: User, session: AsyncSession, request: Request) -> ProviderService:
    org_id = await resolve_current_org_id(user, session)
    cred_service = build_credential_service(
        session,
        request.app.state.encryption_backend,
        org_id=org_id,
        actor_user_id=user.id,
    )
    return ProviderService(
        provider_repo=ProviderRepository(session, org_id=org_id),
        model_repo=ModelRepository(session),
        override_repo=OrgProviderOverrideRepository(session, org_id=org_id),
        credential_service=cred_service,
        session=session,
        org_id=org_id,
        actor_user_id=user.id,
    )


def _model_out(m: Model) -> ModelOut:
    return ModelOut(
        id=m.id,
        provider_id=m.provider_id,
        model_id=m.model_id,
        display_name=m.display_name,
        reasoning=m.reasoning,
        input_modalities=m.input_modalities,
        cost_input=m.cost_input,
        cost_output=m.cost_output,
        cost_cache_read=m.cost_cache_read,
        cost_cache_write=m.cost_cache_write,
        context_window=m.context_window,
        max_tokens=m.max_tokens,
        extra_body=m.extra_body,
        extra_headers=m.extra_headers,
        enabled=m.enabled,
        is_system=m.org_id is None,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _model_readiness_out(m: Model, p: Provider) -> ModelReadinessOut:
    """Build a model row with per-model status + server-derived readiness.

    The `stale` signal compares the provider's current capability fingerprint
    against the one stored in the model's `last_test_summary` by the probe. When
    no fingerprint is stored yet (the common case until the probe persists one),
    capability is treated as unchanged (not stale).
    """
    summary = m.last_test_summary or {}
    stored_fp = summary.get("capability_fingerprint")
    if stored_fp is None:
        capability_changed = False
    else:
        current_fp = capability_fingerprint(p.capability, p.model_capability_overrides)
        capability_changed = stored_fp != current_fp

    readiness = derive_readiness(
        liveness_status=p.last_liveness_status,
        model_test_status=m.last_test_status,
        capability_changed_since_test=capability_changed,
    )
    return ModelReadinessOut(
        id=m.id,
        provider_id=m.provider_id,
        model_id=m.model_id,
        display_name=m.display_name,
        reasoning=m.reasoning,
        input_modalities=m.input_modalities,
        cost_input=m.cost_input,
        cost_output=m.cost_output,
        cost_cache_read=m.cost_cache_read,
        cost_cache_write=m.cost_cache_write,
        context_window=m.context_window,
        max_tokens=m.max_tokens,
        extra_body=m.extra_body,
        extra_headers=m.extra_headers,
        enabled=m.enabled,
        is_system=m.org_id is None,
        created_at=m.created_at,
        updated_at=m.updated_at,
        last_test_at=utc_isoformat(m.last_test_at) if m.last_test_at else None,
        last_test_status=m.last_test_status,
        last_test_summary=summary,
        readiness=readiness,
    )


def _resolve_logo(preset_slug: str | None) -> str | None:
    """Resolve the brand-icon id from the catalog vendor. None if unknown.

    Goes through ``catalog.resolve`` so ``key:`` overrides (preset_keys that do
    not start with the vendor) still resolve — not a ``split("/")`` shortcut.
    """
    if not preset_slug:
        return None
    try:
        from cubebox.llm.catalog import load_catalog

        catalog = load_catalog()
        ep = catalog.resolve(preset_slug)
        for v in catalog.vendors:
            if v.vendor == ep.vendor:
                return v.logo
    except Exception:
        return None
    return None


def _provider_out(
    p: Provider,
    model_count: int = 0,
    models: list[Model] | None = None,
    override: OrgProviderOverride | None = None,
) -> ProviderOut:
    return ProviderOut(
        id=p.id,
        name=p.name,
        slug=p.slug,
        provider_type=p.provider_type,
        base_url=p.base_url,
        auth_type=p.auth_type,
        has_api_key=bool(p.credential_id),
        logo_url=p.logo_url,
        enabled=p.enabled,
        is_system=p.org_id is None,
        model_count=model_count,
        models=[_model_readiness_out(m, p) for m in models] if models is not None else None,
        org_override=OrgProviderOverrideOut(enabled=override.enabled) if override else None,
        extra_body=p.extra_body,
        extra_headers=p.extra_headers,
        preset_slug=p.preset_slug,
        logo=_resolve_logo(p.preset_slug),
        capability=p.capability or {},
        model_capability_overrides=p.model_capability_overrides or {},
        last_liveness_at=utc_isoformat(p.last_liveness_at) if p.last_liveness_at else None,
        last_liveness_status=p.last_liveness_status,
        last_liveness_summary=p.last_liveness_summary or {},
        created_by_user_id=p.created_by_user_id,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


# -- Provider CRUD -----------------------------------------------------------------


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProviderOut]:
    svc = await _svc(user, session, request)
    providers = await svc.list_providers()
    if not providers:
        return []

    provider_ids = [p.id for p in providers]
    count_stmt = (
        select(Model.provider_id, func.count())  # type: ignore[call-overload]
        .where(Model.provider_id.in_(provider_ids))  # type: ignore[attr-defined]
        .group_by(Model.provider_id)
    )
    result = await session.execute(count_stmt)
    counts: dict[str, int] = dict(result.all())  # type: ignore[arg-type]
    return [_provider_out(p, model_count=counts.get(p.id, 0)) for p in providers]


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    body: ProviderCreate,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderOut:
    svc = await _svc(user, session, request)
    try:
        p = await svc.create_provider(body)
    except ProviderOAuthNotImplementedError as e:
        raise HTTPException(
            status_code=409, detail={"code": "provider_oauth_not_implemented"}
        ) from e
    except ProviderNameConflictError as e:
        raise HTTPException(status_code=409, detail={"code": "provider_name_conflict"}) from e
    except ProviderSlugConflictError as e:
        raise HTTPException(status_code=409, detail={"code": "provider_slug_conflict"}) from e
    except InvalidProviderSlugError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_provider_slug"}) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _provider_out(p)


@router.get("/providers/{provider_id}", response_model=ProviderOut)
async def get_provider(
    provider_id: str,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderOut:
    svc = await _svc(user, session, request)
    try:
        p = await svc.get_provider(provider_id)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e

    stmt = (
        select(Model)
        .where(Model.provider_id == provider_id)  # type: ignore[arg-type]
        .order_by(Model.model_id)
    )
    result = await session.execute(stmt)
    models = list(result.scalars().all())
    override = await svc.get_override(provider_id)
    return _provider_out(p, model_count=len(models), models=models, override=override)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: str,
    body: ProviderUpdate,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderOut:
    svc = await _svc(user, session, request)
    try:
        p = await svc.update_provider(provider_id, body)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e
    except ProviderSystemReadonlyError as e:
        raise HTTPException(status_code=403, detail={"code": "provider_system_readonly"}) from e
    except ProviderNameConflictError as e:
        raise HTTPException(status_code=409, detail={"code": "provider_name_conflict"}) from e
    except ProviderOAuthNotImplementedError as e:
        raise HTTPException(
            status_code=409, detail={"code": "provider_oauth_not_implemented"}
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _provider_out(p)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    svc = await _svc(user, session, request)
    try:
        await svc.delete_provider(provider_id)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e
    except ProviderSystemReadonlyError as e:
        raise HTTPException(status_code=403, detail={"code": "provider_system_readonly"}) from e


# -- Model CRUD --------------------------------------------------------------------


@router.post("/providers/{provider_id}/models", response_model=ModelOut, status_code=201)
async def create_model(
    provider_id: str,
    body: ModelCreate,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelOut:
    svc = await _svc(user, session, request)
    try:
        m = await svc.create_model(provider_id, body)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e
    except ProviderSystemReadonlyError as e:
        raise HTTPException(status_code=403, detail={"code": "provider_system_readonly"}) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _model_out(m)


@router.patch("/providers/{provider_id}/models/{mid}", response_model=ModelOut)
async def update_model(
    provider_id: str,
    mid: str,
    body: ModelUpdate,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelOut:
    svc = await _svc(user, session, request)
    try:
        m = await svc.update_model(provider_id, mid, body)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e
    except ProviderSystemReadonlyError as e:
        raise HTTPException(status_code=403, detail={"code": "provider_system_readonly"}) from e
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail="model_not_found") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _model_out(m)


@router.delete("/providers/{provider_id}/models/{mid}", status_code=204)
async def delete_model(
    provider_id: str,
    mid: str,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    svc = await _svc(user, session, request)
    try:
        await svc.delete_model(provider_id, mid)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e
    except ProviderSystemReadonlyError as e:
        raise HTTPException(status_code=403, detail={"code": "provider_system_readonly"}) from e
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail="model_not_found") from e


# -- Test / liveness probe (spec §4.3) ---------------------------------------------


@router.post("/providers/liveness", response_model=ProbeStep)
async def liveness_dryrun(
    body: ProviderLivenessRequest,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProbeStep:
    """Pre-save liveness dry-run. Builds a transient provider; no DB write."""
    svc = await _svc(user, session, request)
    return await svc.run_liveness_dryrun(body)


@router.post("/providers/{provider_id}/liveness", response_model=ProbeStep)
async def liveness_saved(
    provider_id: str,
    body: ModelTest,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProbeStep:
    """Re-check a saved provider's liveness and persist last_liveness_*."""
    svc = await _svc(user, session, request)
    try:
        return await svc.run_liveness_saved(provider_id, body.model_id)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e


@router.post("/providers/test", response_model=ProbeResult)
async def test_provider(
    body: ProviderTestRequest,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProbeResult:
    """Pre-save full probe (liveness + per-model capability). No DB write."""
    svc = await _svc(user, session, request)
    return await svc.run_test_dryrun(body)


@router.post("/providers/{provider_id}/models/{mid}/test", response_model=ProbeResult)
async def test_provider_model(
    provider_id: str,
    mid: str,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProbeResult:
    """Saved single-model test; persists provider liveness + that model's last_test_*."""
    svc = await _svc(user, session, request)
    try:
        return await svc.run_model_test_saved(provider_id, mid)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail="model_not_found") from e


@router.post("/providers/{provider_id}/test", response_model=list[ProbeResult])
async def test_provider_all_models(
    provider_id: str,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProbeResult]:
    """Saved all-enabled-models test; persists provider liveness + each model's last_test_*."""
    svc = await _svc(user, session, request)
    try:
        return await svc.run_all_models_test_saved(provider_id)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/providers/{provider_id}/test/stream")
async def test_provider_stream(
    provider_id: str,
    body: ProviderTestStreamRequest,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Stream liveness (once) + per-model probe events as SSE for the given model db ids."""
    svc = await _svc(user, session, request)
    try:
        await svc.preflight_test_stream(provider_id, body.model_db_ids)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail="model_not_found") from e
    return StreamingResponse(
        svc.run_test_stream(provider_id, body.model_db_ids),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# -- Org provider overrides --------------------------------------------------------


@router.get("/providers/{provider_id}/override", response_model=OrgProviderOverrideOut)
async def get_provider_override(
    provider_id: str,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgProviderOverrideOut:
    svc = await _svc(user, session, request)
    try:
        override = await svc.get_override(provider_id)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e
    if override is None:
        return OrgProviderOverrideOut(enabled=True)
    return OrgProviderOverrideOut(enabled=override.enabled)


@router.patch("/providers/{provider_id}/override", response_model=OrgProviderOverrideOut)
async def set_provider_override(
    provider_id: str,
    body: OrgProviderOverrideUpdate,
    *,
    request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OrgProviderOverrideOut:
    svc = await _svc(user, session, request)
    try:
        override = await svc.set_override(provider_id, body.enabled)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e
    except ProviderOverrideNotApplicableError as e:
        raise HTTPException(
            status_code=400, detail={"code": "provider_override_not_applicable"}
        ) from e
    return OrgProviderOverrideOut(enabled=override.enabled)
