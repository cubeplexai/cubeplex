"""Unit coverage for org-catalog skill delete (route + cascade + cache purge).

E2E owns the DB/API contract; these tests pin the in-process branches Codecov's
unit upload sees first: 404/422 at the route, install cascade, and object-store
failures that must not roll back a completed catalog delete.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from cubeplex.api.routes.v1 import admin_skills as admin_skills_mod
from cubeplex.api.routes.v1.admin_skills import delete_catalog_skill
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.service import SkillPublishService


def _execute_result(rows: list[object]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_delete_catalog_skill_404_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin_skills_mod, "resolve_current_org_id", AsyncMock(return_value="org-1"))
    repo = SimpleNamespace(get=AsyncMock(return_value=None))
    monkeypatch.setattr(admin_skills_mod, "SkillRepository", lambda _session: repo)

    with pytest.raises(HTTPException) as raised:
        await delete_catalog_skill("skl_missing", user=MagicMock(), session=MagicMock())
    assert raised.value.status_code == 404
    assert raised.value.detail == "SKILL_NOT_FOUND"


@pytest.mark.asyncio
async def test_delete_catalog_skill_404_when_other_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin_skills_mod, "resolve_current_org_id", AsyncMock(return_value="org-1"))
    skill = SimpleNamespace(id="skl-1", source="uploaded", owner_org_id="org-other")
    repo = SimpleNamespace(get=AsyncMock(return_value=skill))
    monkeypatch.setattr(admin_skills_mod, "SkillRepository", lambda _session: repo)

    with pytest.raises(HTTPException) as raised:
        await delete_catalog_skill("skl-1", user=MagicMock(), session=MagicMock())
    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_catalog_skill_rejects_preinstalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin_skills_mod, "resolve_current_org_id", AsyncMock(return_value="org-1"))
    skill = SimpleNamespace(id="skl-1", source="preinstalled", owner_org_id=None)
    repo = SimpleNamespace(get=AsyncMock(return_value=skill))
    monkeypatch.setattr(admin_skills_mod, "SkillRepository", lambda _session: repo)

    with pytest.raises(HTTPException) as raised:
        await delete_catalog_skill("skl-1", user=MagicMock(), session=MagicMock())
    assert raised.value.status_code == 422
    assert raised.value.detail == {"code": "CANNOT_DELETE_PREINSTALLED"}


@pytest.mark.asyncio
async def test_delete_catalog_skill_calls_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin_skills_mod, "resolve_current_org_id", AsyncMock(return_value="org-1"))
    skill = SimpleNamespace(id="skl-1", source="uploaded", owner_org_id="org-1")
    repo = SimpleNamespace(get=AsyncMock(return_value=skill))
    monkeypatch.setattr(admin_skills_mod, "SkillRepository", lambda _session: repo)
    publisher = SimpleNamespace(delete_uploaded=AsyncMock())
    monkeypatch.setattr(admin_skills_mod, "SkillPublishService", lambda **_kw: publisher)
    monkeypatch.setattr(admin_skills_mod, "_cache", lambda: MagicMock())

    await delete_catalog_skill("skl-1", user=MagicMock(), session=MagicMock())
    publisher.delete_uploaded.assert_awaited_once_with(org_id="org-1", skill=skill)


def _publisher(
    session: MagicMock,
    cache: MagicMock,
    *,
    versions: list[object],
    monkeypatch: pytest.MonkeyPatch,
) -> SkillPublishService:
    monkeypatch.setattr(
        "cubeplex.skills.service.SkillVersionRepository",
        lambda _session: SimpleNamespace(list_for_skill=AsyncMock(return_value=versions)),
    )
    return SkillPublishService(session=session, cache=cache)


@pytest.mark.asyncio
async def test_delete_uploaded_cascades_installs_and_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(return_value=_execute_result([SimpleNamespace(id="osi-1")]))

    version = SimpleNamespace(id="sklv-1", storage_prefix="skills/org/foo/1.0.0/")
    cache = MagicMock()
    store = MagicMock()
    store.list_objects = AsyncMock(return_value=["skills/org/foo/1.0.0/SKILL.md"])
    store.delete_file = AsyncMock()
    monkeypatch.setattr("cubeplex.skills.service.get_objectstore_client", lambda: store)

    skill = SimpleNamespace(id="skl-1")
    await _publisher(session, cache, versions=[version], monkeypatch=monkeypatch).delete_uploaded(
        org_id="org-1", skill=skill
    )

    assert session.flush.await_count == 1
    session.commit.assert_awaited_once()
    store.delete_file.assert_awaited_once_with("skills/org/foo/1.0.0/SKILL.md")
    cache.purge.assert_called_once_with("sklv-1")


@pytest.mark.asyncio
async def test_delete_uploaded_skips_binding_delete_when_no_installs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(return_value=_execute_result([]))

    cache = MagicMock()
    store = MagicMock()
    store.list_objects = AsyncMock(return_value=[])
    monkeypatch.setattr("cubeplex.skills.service.get_objectstore_client", lambda: store)

    skill = SimpleNamespace(id="skl-1")
    await _publisher(session, cache, versions=[], monkeypatch=monkeypatch).delete_uploaded(
        org_id="org-1", skill=skill
    )

    session.flush.assert_not_awaited()
    session.commit.assert_awaited_once()
    cache.purge.assert_not_called()


@pytest.mark.asyncio
async def test_delete_uploaded_survives_objectstore_list_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(return_value=_execute_result([]))

    version = SimpleNamespace(id="sklv-1", storage_prefix="skills/org/foo/1.0.0/")
    cache = MagicMock()
    store = MagicMock()
    store.list_objects = AsyncMock(side_effect=RuntimeError("s3 down"))
    store.delete_file = AsyncMock()
    monkeypatch.setattr("cubeplex.skills.service.get_objectstore_client", lambda: store)

    await _publisher(session, cache, versions=[version], monkeypatch=monkeypatch).delete_uploaded(
        org_id="org-1", skill=SimpleNamespace(id="skl-1")
    )

    store.delete_file.assert_not_awaited()
    cache.purge.assert_called_once_with("sklv-1")


@pytest.mark.asyncio
async def test_delete_uploaded_survives_objectstore_delete_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(return_value=_execute_result([]))

    version = SimpleNamespace(id="sklv-1", storage_prefix="skills/org/foo/1.0.0/")
    cache = MagicMock()
    store = MagicMock()
    store.list_objects = AsyncMock(return_value=["skills/org/foo/1.0.0/SKILL.md"])
    store.delete_file = AsyncMock(side_effect=RuntimeError("delete failed"))
    monkeypatch.setattr("cubeplex.skills.service.get_objectstore_client", lambda: store)

    await _publisher(session, cache, versions=[version], monkeypatch=monkeypatch).delete_uploaded(
        org_id="org-1", skill=SimpleNamespace(id="skl-1")
    )

    cache.purge.assert_called_once_with("sklv-1")


def test_skill_cache_purge_removes_dir_and_lock(tmp_path: Path) -> None:
    cache = SkillCache(cache_root=tmp_path)
    version_id = "sklv-1"
    target = cache.cache_dir(version_id)
    target.mkdir()
    (target / "SKILL.md").write_text("x")
    cache._lock_for(version_id)
    assert version_id in cache._locks

    cache.purge(version_id)

    assert not target.exists()
    assert version_id not in cache._locks


def test_skill_cache_purge_missing_dir_is_noop(tmp_path: Path) -> None:
    cache = SkillCache(cache_root=tmp_path)
    cache.purge("sklv-absent")
    assert not cache.cache_dir("sklv-absent").exists()
