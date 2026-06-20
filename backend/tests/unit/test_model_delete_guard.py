"""Guard against deleting a model that is referenced by caller-org presets.

Per D6 (updated):
- Scan the caller's org row of OrgSettings.model_presets first.
- If no org row exists, fall back to the system row — those are the
  caller's effective presets, so a delete that ignored them would break
  the next agent run.
- Never scan another org's row (cross-tenant info leak).
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubebox.api.exceptions import ModelInUseByPresetError
from cubebox.credentials.encryption import FernetBackend
from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubebox.models.provider import Model, Provider
from cubebox.repositories.credential import CredentialRepository
from cubebox.repositories.model import ModelRepository
from cubebox.repositories.org_provider_override import OrgProviderOverrideRepository
from cubebox.repositories.provider import ProviderRepository
from cubebox.services.credential import CredentialService
from cubebox.services.provider_service import ProviderService

CALLER_ORG = "org-caller"
OTHER_ORG = "org-other"


def _off_tiers() -> dict[str, dict[str, object]]:
    return {
        t: {"enabled": False, "primary": None, "fallbacks": []}
        for t in ("lite", "flash", "pro", "max")
    }


def _config_with_custom(label: str, ref: str) -> dict[str, object]:
    """A ModelPresetsConfig whose only available preset is a custom one
    named ``label`` pointing at ``ref`` (the default)."""
    return {
        "tiers": _off_tiers(),
        "custom_presets": [{"label": label, "primary": ref, "fallbacks": []}],
        "default_preset": label,
        "task_routing": {},
    }


def _empty_config() -> dict[str, object]:
    """A config with one enabled tier (so default_preset is satisfiable)
    that does NOT reference acme/m1 — used as a shadowing org row."""
    tiers = _off_tiers()
    tiers["pro"] = {"enabled": True, "primary": "other/x", "fallbacks": []}
    return {
        "tiers": tiers,
        "custom_presets": [],
        "default_preset": "pro",
        "task_routing": {},
    }


@pytest.fixture()
async def db_session():
    """In-memory SQLite session for fast unit tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


def _make_svc(session: AsyncSession, org_id: str = CALLER_ORG) -> ProviderService:
    backend = FernetBackend([Fernet.generate_key()])
    cred_service = CredentialService(
        CredentialRepository(session, org_id=org_id),
        backend,
        org_id=org_id,
        actor_user_id="user-1",
    )
    return ProviderService(
        provider_repo=ProviderRepository(session, org_id=org_id),
        model_repo=ModelRepository(session),
        override_repo=OrgProviderOverrideRepository(session, org_id=org_id),
        credential_service=cred_service,
        session=session,
        org_id=org_id,
        actor_user_id="user-1",
    )


async def _seed_provider_with_model(
    session: AsyncSession,
    *,
    org_id: str,
    slug: str = "acme",
    model_id: str = "m1",
) -> tuple[str, str]:
    """Create a provider + model in the given org, return (provider_id, model_db_id)."""
    p = Provider(
        org_id=org_id,
        name=f"{slug}-provider",
        slug=slug,
        base_url="https://example.com",
        auth_type="none",
        created_by_user_id="user-1",
    )
    session.add(p)
    await session.flush()
    m = Model(
        provider_id=p.id,
        model_id=model_id,
        display_name="Test",
        context_window=8192,
        max_tokens=2048,
    )
    session.add(m)
    await session.commit()
    return p.id, m.id


async def test_delete_blocked_when_caller_org_preset_references_model(
    db_session: AsyncSession,
) -> None:
    """Caller org has a preset referencing the model → 409 with that label only."""
    pid, mid = await _seed_provider_with_model(db_session, org_id=CALLER_ORG)
    db_session.add(
        OrgSettings(
            org_id=CALLER_ORG,
            key=MODEL_PRESETS_KEY,
            value=_config_with_custom("ultra", "acme/m1"),
        )
    )
    await db_session.commit()

    svc = _make_svc(db_session)
    with pytest.raises(ModelInUseByPresetError) as exc:
        await svc.delete_model(pid, mid)
    assert exc.value.status_code == 409
    assert exc.value.refs == [
        {"org_id": CALLER_ORG, "preset_label": "ultra", "source": "org"},
    ]


async def test_delete_allowed_when_only_other_org_references_model(
    db_session: AsyncSession,
) -> None:
    """Another org references the model → delete proceeds; we do not scan other orgs."""
    pid, mid = await _seed_provider_with_model(db_session, org_id=CALLER_ORG)
    # Another org has a preset referencing acme/m1; must NOT block (and labels
    # must NOT leak across orgs via a 409 body).
    db_session.add(
        OrgSettings(
            org_id=OTHER_ORG,
            key=MODEL_PRESETS_KEY,
            value=_config_with_custom("secret-other-org", "acme/m1"),
        )
    )
    await db_session.commit()

    svc = _make_svc(db_session)
    await svc.delete_model(pid, mid)
    # No exception → success. Confirm the model is gone.
    assert await ModelRepository(db_session).get(mid) is None


async def test_delete_blocked_when_only_system_row_references_model(
    db_session: AsyncSession,
) -> None:
    """System row references the model, no caller-org row → delete blocked.

    When the org has no own row, the system row is the caller's effective
    presets — deleting a referenced model would surface as broken_preset
    on the next agent run. Surface that as a 409 at delete time instead.
    """
    pid, mid = await _seed_provider_with_model(db_session, org_id=CALLER_ORG)
    db_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value=_config_with_custom("sys-default", "acme/m1"),
        )
    )
    await db_session.commit()

    svc = _make_svc(db_session)
    with pytest.raises(ModelInUseByPresetError) as exc:
        await svc.delete_model(pid, mid)
    assert exc.value.status_code == 409
    assert exc.value.refs == [
        # System-row refs surface as org_id=None (no org owns the system row).
        {"org_id": None, "preset_label": "sys-default", "source": "system"},
    ]
    # Model row survives — guard fired before delete.
    assert await ModelRepository(db_session).get(mid) is not None


async def test_delete_allowed_when_system_row_references_but_org_row_exists(
    db_session: AsyncSession,
) -> None:
    """Org row exists (even if empty) → the system row is no longer effective.

    Once the org has saved its own model_presets, the system row is shadowed.
    Whatever references it carries are irrelevant for the org's runs, so the
    delete proceeds.
    """
    pid, mid = await _seed_provider_with_model(db_session, org_id=CALLER_ORG)
    db_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value=_config_with_custom("sys-default", "acme/m1"),
        )
    )
    db_session.add(
        OrgSettings(
            org_id=CALLER_ORG,
            key=MODEL_PRESETS_KEY,
            value=_empty_config(),
        )
    )
    await db_session.commit()

    svc = _make_svc(db_session)
    await svc.delete_model(pid, mid)
    assert await ModelRepository(db_session).get(mid) is None


async def test_delete_allowed_when_no_preset_references_model(
    db_session: AsyncSession,
) -> None:
    """Nothing references the model → delete proceeds normally."""
    pid, mid = await _seed_provider_with_model(db_session, org_id=CALLER_ORG)
    svc = _make_svc(db_session)
    await svc.delete_model(pid, mid)
    assert await ModelRepository(db_session).get(mid) is None
