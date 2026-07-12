"""E2E tests for runtime status writeback (spec §4.4a).

Insert real Provider + Model rows, drive ``_do_writeback`` (which opens its own
session via ``async_session_maker`` — patched to the test DB by the e2e
conftest), then assert the persisted columns flipped at the right grain:

- auth error -> provider ``last_liveness_status`` = "fail" (provider-grain).
- model_not_found -> that model ``last_test_status`` = "unavailable", siblings
  untouched (model-grain).
- success after fail -> guarded UPDATE flips liveness back to "ok"; a healthy
  (NULL) provider is left untouched.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.llm.runtime_writeback import _do_writeback
from cubeplex.models.provider import Model, Provider

pytestmark = pytest.mark.e2e

_ORG_ID = "org-00000000000000"  # DEFAULT_ORG_ID seeded by the e2e conftest


async def _seed_provider_with_models(
    session: AsyncSession,
    *,
    name: str,
    liveness_status: str | None,
) -> tuple[str, str, str]:
    """Insert a system-scoped (org-NULL) provider + two models. Returns
    (provider_id, model_a_db_id, model_b_db_id)."""
    from cubeplex.utils.slug import slugify

    provider = Provider(
        org_id=None,
        name=name,
        slug=slugify(name),
        base_url="https://example.com/api",
        last_liveness_status=liveness_status,
    )
    session.add(provider)
    await session.flush()
    model_a = Model(
        org_id=None,
        provider_id=provider.id,
        model_id="model-a",
        display_name="Model A",
        context_window=8192,
        max_tokens=1024,
        last_test_status="ok",
    )
    model_b = Model(
        org_id=None,
        provider_id=provider.id,
        model_id="model-b",
        display_name="Model B",
        context_window=8192,
        max_tokens=1024,
        last_test_status="ok",
    )
    session.add(model_a)
    session.add(model_b)
    await session.commit()
    return provider.id, model_a.id, model_b.id


async def test_auth_error_flips_provider_liveness_fail(db_session: AsyncSession) -> None:
    from cubeplex.utils.slug import slugify

    name = f"rt-writeback-auth-{uuid.uuid4().hex[:8]}"
    provider_id, _, _ = await _seed_provider_with_models(
        db_session, name=name, liveness_status="ok"
    )

    await _do_writeback(
        org_id=_ORG_ID,
        provider_slug=slugify(name),
        model_id="model-a",
        outcome="auth_error",
        summary="HTTPStatusError: 401 Unauthorized",
    )

    refreshed = await db_session.get(Provider, provider_id)
    assert refreshed is not None
    await db_session.refresh(refreshed)
    assert refreshed.last_liveness_status == "fail"
    assert refreshed.last_liveness_at is not None
    assert refreshed.last_liveness_summary.get("source") == "runtime"


async def test_model_not_found_flips_only_that_model(db_session: AsyncSession) -> None:
    from cubeplex.utils.slug import slugify

    name = f"rt-writeback-modelnf-{uuid.uuid4().hex[:8]}"
    provider_id, model_a_id, model_b_id = await _seed_provider_with_models(
        db_session, name=name, liveness_status="ok"
    )

    await _do_writeback(
        org_id=_ORG_ID,
        provider_slug=slugify(name),
        model_id="model-a",
        outcome="model_not_found",
        summary="404 model_not_found",
    )

    model_a = await db_session.get(Model, model_a_id)
    model_b = await db_session.get(Model, model_b_id)
    assert model_a is not None and model_b is not None
    await db_session.refresh(model_a)
    await db_session.refresh(model_b)
    # Target model flipped to unavailable…
    assert model_a.last_test_status == "unavailable"
    assert model_a.last_test_at is not None
    # …sibling untouched.
    assert model_b.last_test_status == "ok"
    # Provider liveness untouched — model_not_found is model-grain only.
    provider = await db_session.get(Provider, provider_id)
    assert provider is not None
    await db_session.refresh(provider)
    assert provider.last_liveness_status == "ok"


async def test_success_clears_only_a_failed_provider(db_session: AsyncSession) -> None:
    from cubeplex.utils.slug import slugify

    # Provider currently failing -> success clears it back to "ok".
    name_fail = f"rt-writeback-clear-{uuid.uuid4().hex[:8]}"
    provider_id_fail, _, _ = await _seed_provider_with_models(
        db_session, name=name_fail, liveness_status="fail"
    )
    await _do_writeback(
        org_id=_ORG_ID,
        provider_slug=slugify(name_fail),
        model_id="model-a",
        outcome="other",
        summary="runtime call succeeded",
    )
    provider = await db_session.get(Provider, provider_id_fail)
    assert provider is not None
    await db_session.refresh(provider)
    assert provider.last_liveness_status == "ok"


async def test_success_is_noop_for_healthy_provider(db_session: AsyncSession) -> None:
    from cubeplex.utils.slug import slugify

    # Guarded UPDATE: a provider that was never tested (NULL) stays NULL —
    # success must not invent an "ok" out of nowhere.
    name_ok = f"rt-writeback-noop-{uuid.uuid4().hex[:8]}"
    provider_id_ok, _, _ = await _seed_provider_with_models(
        db_session, name=name_ok, liveness_status=None
    )
    await _do_writeback(
        org_id=_ORG_ID,
        provider_slug=slugify(name_ok),
        model_id="model-a",
        outcome="other",
        summary="runtime call succeeded",
    )
    provider = await db_session.get(Provider, provider_id_ok)
    assert provider is not None
    await db_session.refresh(provider)
    assert provider.last_liveness_status is None
