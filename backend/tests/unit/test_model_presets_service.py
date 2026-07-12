"""Model-presets service layer (tiered ModelPresetsConfig shape)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubeplex.api.schemas.model_presets import AdminModelPresetsBody
from cubeplex.llm.errors import BrokenPresetError
from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubeplex.services.model_presets import (
    find_preset_refs_to_model,
    read_org_presets,
    write_org_presets,
)


def _tiers(**enabled: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for t in ("lite", "flash", "pro", "max"):
        if t in enabled:
            out[t] = {"enabled": True, "primary": enabled[t], "fallbacks": []}
        else:
            out[t] = {"enabled": False, "primary": None, "fallbacks": []}
    return out


def _config(
    *,
    tiers: dict[str, dict[str, Any]],
    default_preset: str,
    custom_presets: list[dict[str, Any]] | None = None,
    task_routing: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "tiers": tiers,
        "custom_presets": custom_presets or [],
        "default_preset": default_preset,
        "task_routing": task_routing or {},
    }


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
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
async def test_read_returns_system_when_no_org_row(session: AsyncSession) -> None:
    session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value=_config(tiers=_tiers(pro="a/b"), default_preset="pro"),
        )
    )
    await session.commit()
    body, origin = await read_org_presets(session, "org_x")
    assert origin == "system"
    assert body is not None
    assert body.tiers["pro"].primary == "a/b"


@pytest.mark.asyncio
async def test_read_returns_org_when_present(session: AsyncSession) -> None:
    session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value=_config(tiers=_tiers(pro="a/b"), default_preset="pro"),
        )
    )
    session.add(
        OrgSettings(
            org_id="org_x",
            key=MODEL_PRESETS_KEY,
            value=_config(tiers=_tiers(flash="a/c"), default_preset="flash"),
        )
    )
    await session.commit()
    body, origin = await read_org_presets(session, "org_x")
    assert origin == "org"
    assert body is not None
    assert body.tiers["flash"].primary == "a/c"


@pytest.mark.asyncio
async def test_read_returns_empty_when_neither_exists(session: AsyncSession) -> None:
    body, origin = await read_org_presets(session, "org_x")
    assert origin == "none"
    assert body is None


@pytest.mark.asyncio
async def test_write_upserts_org_row(session: AsyncSession) -> None:
    body = AdminModelPresetsBody.model_validate(
        _config(tiers=_tiers(pro="acme/m1"), default_preset="pro")
    )
    await write_org_presets(session, "org_x", body, available_models={"acme/m1"})
    await session.commit()
    body2, origin = await read_org_presets(session, "org_x")
    assert origin == "org"
    assert body2 is not None
    assert body2.tiers["pro"].primary == "acme/m1"


@pytest.mark.asyncio
async def test_write_rejects_unknown_ref(session: AsyncSession) -> None:
    body = AdminModelPresetsBody.model_validate(
        _config(tiers=_tiers(pro="ghost/x"), default_preset="pro")
    )
    with pytest.raises(BrokenPresetError) as exc:
        await write_org_presets(session, "org_x", body, available_models={"acme/m1"})
    assert "ghost/x" in exc.value.missing_refs


@pytest.mark.asyncio
async def test_write_validates_custom_preset_chains(session: AsyncSession) -> None:
    body = AdminModelPresetsBody.model_validate(
        _config(
            tiers=_tiers(pro="acme/m1"),
            default_preset="pro",
            custom_presets=[{"label": "fancy", "primary": "acme/m1", "fallbacks": ["ghost/y"]}],
        )
    )
    with pytest.raises(BrokenPresetError) as exc:
        await write_org_presets(session, "org_x", body, available_models={"acme/m1"})
    assert "ghost/y" in exc.value.missing_refs


@pytest.mark.asyncio
async def test_find_preset_refs_to_model_scans_org_row(session: AsyncSession) -> None:
    session.add(
        OrgSettings(
            org_id="org_x",
            key=MODEL_PRESETS_KEY,
            value=_config(
                tiers=_tiers(pro="acme/m1"),
                default_preset="pro",
                custom_presets=[
                    {"label": "ultra", "primary": "acme/m1", "fallbacks": ["acme/m2"]},
                    {"label": "mini", "primary": "acme/m1"},
                ],
            ),
        )
    )
    await session.commit()
    refs = await find_preset_refs_to_model(session, "org_x", "acme", "m1")
    assert {r["preset_label"] for r in refs} == {"pro", "ultra", "mini"}
    assert all(r["source"] == "org" for r in refs)
    refs2 = await find_preset_refs_to_model(session, "org_x", "acme", "m2")
    assert refs2 == [{"preset_label": "ultra", "source": "org"}]
    refs3 = await find_preset_refs_to_model(session, "org_x", "ghost", "x")
    assert refs3 == []


@pytest.mark.asyncio
async def test_find_preset_refs_falls_back_to_system_when_no_org_row(
    session: AsyncSession,
) -> None:
    session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value=_config(tiers=_tiers(pro="acme/m1"), default_preset="pro"),
        )
    )
    await session.commit()
    refs = await find_preset_refs_to_model(session, "org_x", "acme", "m1")
    assert refs == [{"preset_label": "pro", "source": "system"}]


@pytest.mark.asyncio
async def test_find_preset_refs_org_row_supersedes_system(session: AsyncSession) -> None:
    session.add(
        OrgSettings(
            org_id=None,
            key=MODEL_PRESETS_KEY,
            value=_config(tiers=_tiers(pro="acme/m1"), default_preset="pro"),
        )
    )
    session.add(
        OrgSettings(
            org_id="org_x",
            key=MODEL_PRESETS_KEY,
            value=_config(tiers=_tiers(flash="acme/m9"), default_preset="flash"),
        )
    )
    await session.commit()
    refs = await find_preset_refs_to_model(session, "org_x", "acme", "m1")
    assert refs == []
