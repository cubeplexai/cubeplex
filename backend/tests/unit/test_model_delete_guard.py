"""Guard against deleting a model that is referenced by caller-org presets.

Per D6:
- Scan only the caller's org row of OrgSettings.model_presets.
- Skip the system row (chicken-and-egg trap).
- Do not scan other orgs (cross-tenant info leak).
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
from cubebox.repositories.org_settings import OrgSettingsRepository
from cubebox.repositories.provider import ProviderRepository
from cubebox.services.credential import CredentialService
from cubebox.services.provider_service import ProviderService

CALLER_ORG = "org-caller"
OTHER_ORG = "org-other"


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
        org_settings_repo=OrgSettingsRepository(session, org_id=org_id),
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
            value={
                "presets": [
                    {"label": "ultra", "chain": ["acme/m1"], "is_default": True},
                ],
                "task_presets": {},
            },
        )
    )
    await db_session.commit()

    svc = _make_svc(db_session)
    with pytest.raises(ModelInUseByPresetError) as exc:
        await svc.delete_model(pid, mid)
    assert exc.value.status_code == 409
    assert exc.value.refs == [{"org_id": CALLER_ORG, "preset_label": "ultra"}]


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
            value={
                "presets": [
                    {"label": "secret-other-org", "chain": ["acme/m1"], "is_default": True},
                ],
                "task_presets": {},
            },
        )
    )
    await db_session.commit()

    svc = _make_svc(db_session)
    await svc.delete_model(pid, mid)
    # No exception → success. Confirm the model is gone.
    assert await ModelRepository(db_session).get(mid) is None


async def test_delete_allowed_when_only_system_row_references_model(
    db_session: AsyncSession,
) -> None:
    """System row references the model, no caller-org row exists → delete proceeds.

    Per D6: system row is intentionally skipped (chicken-and-egg — admin must be
    able to delete a model that the seed presets reference, before they have
    saved their first org-level override).
    """
    pid, mid = await _seed_provider_with_model(db_session, org_id=CALLER_ORG)
    db_session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [
                    {"label": "sys-default", "chain": ["acme/m1"], "is_default": True},
                ],
                "task_presets": {},
            },
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
