"""Model-presets service layer."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubebox.api.schemas.model_presets import AdminModelPresetsBody
from cubebox.llm.errors import BrokenPresetError
from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubebox.services.model_presets import (
    find_preset_refs_to_model,
    read_org_presets,
    write_org_presets,
)


@pytest_asyncio.fixture
async def session():
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


@pytest.mark.asyncio
async def test_read_returns_system_when_no_org_row(session):
    session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [{"label": "sys", "chain": ["a/b"], "is_default": True}],
                "task_presets": {},
            },
        )
    )
    await session.commit()
    body, origin = await read_org_presets(session, "org_x")
    assert origin == "system"
    assert body.presets[0].label == "sys"


@pytest.mark.asyncio
async def test_read_returns_org_when_present(session):
    session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [{"label": "sys", "chain": ["a/b"], "is_default": True}],
                "task_presets": {},
            },
        )
    )
    session.add(
        OrgSettings(
            org_id="org_x",
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [{"label": "org", "chain": ["a/b"], "is_default": True}],
                "task_presets": {},
            },
        )
    )
    await session.commit()
    body, origin = await read_org_presets(session, "org_x")
    assert origin == "org"
    assert body.presets[0].label == "org"


@pytest.mark.asyncio
async def test_read_returns_empty_when_neither_exists(session):
    body, origin = await read_org_presets(session, "org_x")
    assert origin == "none"
    assert body is None


@pytest.mark.asyncio
async def test_write_upserts_org_row(session):
    body = AdminModelPresetsBody.model_validate(
        {
            "presets": [{"label": "default", "chain": ["acme/m1"], "is_default": True}],
            "task_presets": {},
        }
    )
    await write_org_presets(session, "org_x", body, available_models={"acme/m1"})
    await session.commit()
    body2, origin = await read_org_presets(session, "org_x")
    assert origin == "org"
    assert body2.presets[0].label == "default"


@pytest.mark.asyncio
async def test_write_rejects_unknown_ref(session):
    body = AdminModelPresetsBody.model_validate(
        {
            "presets": [{"label": "default", "chain": ["ghost/x"], "is_default": True}],
            "task_presets": {},
        }
    )
    with pytest.raises(BrokenPresetError) as exc:
        await write_org_presets(session, "org_x", body, available_models={"acme/m1"})
    assert "ghost/x" in exc.value.missing_refs


@pytest.mark.asyncio
async def test_find_preset_refs_to_model_scans_org_row(session):
    session.add(
        OrgSettings(
            org_id="org_x",
            key=MODEL_PRESETS_KEY,
            value={
                "presets": [
                    {"label": "ultra", "chain": ["acme/m1", "acme/m2"], "is_default": True},
                    {"label": "mini", "chain": ["acme/m1"], "is_default": False},
                ],
                "task_presets": {"title": "mini"},
            },
        )
    )
    await session.commit()
    refs = await find_preset_refs_to_model(session, "org_x", "acme", "m1")
    assert {r["preset_label"] for r in refs} == {"ultra", "mini"}
    assert all(r["source"] == "org" for r in refs)
    refs2 = await find_preset_refs_to_model(session, "org_x", "acme", "m2")
    assert refs2 == [{"preset_label": "ultra", "source": "org"}]
    refs3 = await find_preset_refs_to_model(session, "org_x", "ghost", "x")
    assert refs3 == []


@pytest.mark.asyncio
async def test_find_preset_refs_falls_back_to_system_when_no_org_row(session):
    # No org row exists; the system row references the model. Deleting that
    # model would break the org's next run, so the guard must catch it.
    session.add(
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
    await session.commit()
    refs = await find_preset_refs_to_model(session, "org_x", "acme", "m1")
    assert refs == [{"preset_label": "sys-default", "source": "system"}]


@pytest.mark.asyncio
async def test_find_preset_refs_org_row_supersedes_system(session):
    # When the org has its own row, the system row is invisible — that row
    # is no longer the org's effective config.
    session.add(
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
    session.add(
        OrgSettings(
            org_id="org_x",
            key=MODEL_PRESETS_KEY,
            value={"presets": [], "task_presets": {}},
        )
    )
    await session.commit()
    refs = await find_preset_refs_to_model(session, "org_x", "acme", "m1")
    assert refs == []
