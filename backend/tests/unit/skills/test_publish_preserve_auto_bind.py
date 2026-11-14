"""Unit test ensuring org-wide skill publish preserves existing auto_bind on upgrade."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.skills import service as skills_service_mod
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.service import SkillPublishService


def _zip_for(name: str, version: str) -> bytes:
    buf = io.BytesIO()
    md = f"---\nname: {name}\ndescription: desc\nversion: {version}\n---\n# {version}\n"
    with zipfile.ZipFile(buf, mode="w") as z:
        z.writestr("SKILL.md", md.encode())
    return buf.getvalue()


@pytest.mark.asyncio
async def test_publish_org_wide_passes_auto_bind_none_to_upsert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org-wide publish must pass auto_bind=None so upgrades preserve existing auto_bind."""
    existing_skill = SimpleNamespace(id="skl-1", name="org-1:demo", source="uploaded")
    skills_repo = SimpleNamespace(
        find_by_name=AsyncMock(return_value=existing_skill),
        update_current_version=AsyncMock(),
    )
    versions_repo = SimpleNamespace(
        find=AsyncMock(return_value=None),
        list_for_skill=AsyncMock(return_value=[]),
        create=AsyncMock(
            return_value=SimpleNamespace(id="sv-2", skill_id="skl-1", version="0.2.0")
        ),
    )
    upsert_mock = AsyncMock()
    installs_repo = SimpleNamespace(upsert=upsert_mock)
    store = SimpleNamespace(upload_file=AsyncMock())

    monkeypatch.setattr(skills_service_mod, "SkillRepository", lambda _s: skills_repo)
    monkeypatch.setattr(skills_service_mod, "SkillVersionRepository", lambda _s: versions_repo)
    monkeypatch.setattr(skills_service_mod, "OrgSkillInstallRepository", lambda _s: installs_repo)
    monkeypatch.setattr(skills_service_mod, "get_objectstore_client", lambda: store)

    publisher = SkillPublishService(
        session=MagicMock(),
        cache=SkillCache(cache_root=tmp_path / "cache"),
    )
    await publisher.publish_from_zip(
        org_id="org-1",
        org_slug="org-1",
        actor_user_id="usr-1",
        zip_bytes=_zip_for("demo", "0.2.0"),
        workspace_id=None,
    )

    upsert_mock.assert_awaited_once()
    assert upsert_mock.call_args.kwargs["auto_bind"] is None
    assert upsert_mock.call_args.kwargs["installed_version"] == "0.2.0"
