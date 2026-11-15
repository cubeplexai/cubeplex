"""Unit test ensuring org-wide skill publish preserves existing auto_bind on upgrade."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.skills import discovery as skills_discovery_mod
from cubeplex.skills import service as skills_service_mod
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.discovery import SkillInstallService
from cubeplex.skills.service import SkillPublishService, VersionCollisionError
from cubeplex.skills.sources.base import TrustTier


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "existing_install", "expected_auto_bind"),
    [
        ("uploaded", SimpleNamespace(auto_bind=True), None),
        ("preinstalled", SimpleNamespace(auto_bind=False), None),
        ("preinstalled", None, True),
        ("uploaded", None, False),
    ],
)
async def test_install_local_org_wide_preserves_auto_bind(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    existing_install: SimpleNamespace | None,
    expected_auto_bind: bool | None,
) -> None:
    """Org-wide local discovery install preserves existing auto_bind on reinstall."""
    skill = SimpleNamespace(
        id="skl-1",
        name="demo",
        source=source,
        owner_org_id="org-1",
        current_version="0.2.0",
    )
    upsert_mock = AsyncMock()
    installs_repo = SimpleNamespace(
        get=AsyncMock(return_value=existing_install),
        upsert=upsert_mock,
    )
    tombstones_repo = SimpleNamespace(get=AsyncMock(return_value=None))

    monkeypatch.setattr(
        skills_discovery_mod,
        "SkillRepository",
        lambda _s: SimpleNamespace(get=AsyncMock(return_value=skill)),
    )
    monkeypatch.setattr(
        skills_discovery_mod,
        "OrgPreinstalledTombstoneRepository",
        lambda _s: tombstones_repo,
    )
    monkeypatch.setattr(skills_discovery_mod, "OrgSkillInstallRepository", lambda _s: installs_repo)

    svc = SkillInstallService(
        session=MagicMock(),
        registry=MagicMock(),
        publisher=MagicMock(),
        org_id="org-1",
        org_slug="org-1",
        workspace_id=None,
        actor_user_id="usr-1",
    )
    await svc._install_local("skl-1", commit=True)

    upsert_mock.assert_awaited_once()
    assert upsert_mock.call_args.kwargs["auto_bind"] is expected_auto_bind


@pytest.mark.asyncio
async def test_install_remote_collision_org_wide_preserves_auto_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org-wide remote VersionCollisionError fallback passes auto_bind=None."""
    md = b"---\nname: demo\ndescription: desc\nversion: 0.2.0\n---\n# body\n"
    adapter = SimpleNamespace(
        trust_for_ref=lambda _ref: TrustTier.official,
        _trust=TrustTier.official,
        fetch=AsyncMock(return_value={"SKILL.md": md}),
    )
    registry = SimpleNamespace(adapter_by_id=lambda _id: adapter)
    publisher = SimpleNamespace(
        _publish_from_files=AsyncMock(
            side_effect=VersionCollisionError(
                "collision", canonical_name="org-1:demo", version="0.2.0"
            )
        )
    )
    existing = SimpleNamespace(id="skl-1", name="org-1:demo", current_version="0.2.0")
    upsert_mock = AsyncMock()
    installs_repo = SimpleNamespace(upsert=upsert_mock)

    monkeypatch.setattr(
        skills_discovery_mod,
        "SkillRepository",
        lambda _s: SimpleNamespace(find_by_name=AsyncMock(return_value=existing)),
    )
    monkeypatch.setattr(
        skills_discovery_mod,
        "SkillVersionRepository",
        lambda _s: SimpleNamespace(find=AsyncMock(return_value=SimpleNamespace(version="0.2.0"))),
    )
    monkeypatch.setattr(skills_discovery_mod, "OrgSkillInstallRepository", lambda _s: installs_repo)

    svc = SkillInstallService(
        session=MagicMock(),
        registry=registry,  # type: ignore[arg-type]
        publisher=publisher,  # type: ignore[arg-type]
        org_id="org-1",
        org_slug="org-1",
        workspace_id=None,
        actor_user_id="usr-1",
    )
    await svc._install_remote("src-1", "skills/demo", commit=True)

    upsert_mock.assert_awaited_once()
    assert upsert_mock.call_args.kwargs["auto_bind"] is None
    assert upsert_mock.call_args.kwargs["installed_version"] == "0.2.0"
