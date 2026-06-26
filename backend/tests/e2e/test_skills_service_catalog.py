"""E2E: SkillCatalogService.list_enabled_for_workspace + fetch_skill_md."""

import secrets

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.objectstore import get_objectstore_client
from cubebox.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
    WorkspaceSkillBindingRepository,
)
from cubebox.skills.cache import SkillCache
from cubebox.skills.service import SkillCatalogService
from cubebox.skills.storage_paths import global_skill_prefix


async def _seed_org_ws_user(
    db_session: AsyncSession,
    org_id: str,
    ws_id: str,
    user_id: str,
) -> None:
    """Seed minimal org, workspace, and user rows for skill catalog tests.

    These FKs were added in the short-id schema migration and are required
    by org_skill_installs.org_id → organizations, workspace_skill_bindings.workspace_id
    → workspaces, and org_skill_installs.installed_by_user_id → users.
    """
    await db_session.execute(
        text(
            "INSERT INTO organizations (id, name, slug, created_at)"
            " VALUES (:id, :name, :slug, NOW()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": org_id, "name": org_id, "slug": org_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO workspaces (id, org_id, name, created_at)"
            " VALUES (:id, :org_id, :name, NOW()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": ws_id, "org_id": org_id, "name": ws_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, is_active, is_superuser,"
            " is_verified, created_at, language)"
            " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": user_id, "email": f"{user_id}@skill-catalog-test.local"},
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_list_enabled_for_workspace(tmp_path, db_session) -> None:
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)
    installs = OrgSkillInstallRepository(db_session)

    org_id = f"org-{secrets.token_hex(4)}"
    ws_id = f"ws-{secrets.token_hex(4)}"
    user_id = "user-1"
    await _seed_org_ws_user(db_session, org_id, ws_id, user_id)
    skill_name = f"deep-research-{secrets.token_hex(4)}"

    skill = await skills.create_preinstalled(
        name=skill_name, description="d", keywords=[], current_version="1.0.0"
    )
    prefix = global_skill_prefix(skill_name, "1.0.0")
    await get_objectstore_client().upload_file(
        f"{prefix}SKILL.md",
        b"---\nname: deep-research\ndescription: d\nversion: 1.0.0\n---\n# DR\n",
    )
    await versions.create(
        skill_id=skill.id,
        version="1.0.0",
        description="d",
        keywords=[],
        raw_metadata={},
        storage_prefix=prefix,
        entry_file="SKILL.md",
        uploaded_by_user_id=None,
        content_hash="",
    )
    install = await installs.upsert(
        org_id=org_id,
        skill_id=skill.id,
        installed_version="1.0.0",
        installed_by_user_id=user_id,
    )

    bindings = WorkspaceSkillBindingRepository(db_session, org_id=org_id, workspace_id=ws_id)
    await bindings.enable(install.id)

    catalog = SkillCatalogService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    resolved = await catalog.list_enabled_for_workspace(ws_id, org_id=org_id)
    assert len(resolved) == 1
    assert resolved[0].name == skill_name
    assert resolved[0].version == "1.0.0"
    # Guard the SELECT → ResolvedSkill projection: if content_hash is dropped
    # from the constructor call in service.py this assertion catches it.
    assert resolved[0].content_hash == ""


@pytest.mark.asyncio
async def test_fetch_skill_md_returns_content(tmp_path, db_session) -> None:
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)

    skill_name = f"x-{secrets.token_hex(4)}"
    skill = await skills.create_preinstalled(
        name=skill_name, description="y", keywords=[], current_version="1.0.0"
    )
    prefix = global_skill_prefix(skill_name, "1.0.0")
    await get_objectstore_client().upload_file(f"{prefix}SKILL.md", b"# Hello\n")
    sv = await versions.create(
        skill_id=skill.id,
        version="1.0.0",
        description="y",
        keywords=[],
        raw_metadata={},
        storage_prefix=prefix,
        entry_file="SKILL.md",
        uploaded_by_user_id=None,
        content_hash="",
    )

    catalog = SkillCatalogService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    content = await catalog.fetch_skill_md(sv.id)
    assert content == "# Hello\n"


@pytest.mark.asyncio
async def test_find_enabled_by_name(tmp_path, db_session) -> None:
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)
    installs = OrgSkillInstallRepository(db_session)

    org_id = f"org-{secrets.token_hex(4)}"
    ws_id = f"ws-{secrets.token_hex(4)}"
    user_id = "user-1"
    skill_name = f"find-skill-{secrets.token_hex(4)}"
    await _seed_org_ws_user(db_session, org_id, ws_id, user_id)

    skill = await skills.create_preinstalled(
        name=skill_name, description="d", keywords=[], current_version="1.0.0"
    )
    prefix = global_skill_prefix(skill_name, "1.0.0")
    await get_objectstore_client().upload_file(
        f"{prefix}SKILL.md",
        b"---\nname: find-skill\ndescription: d\nversion: 1.0.0\n---\n# FS\n",
    )
    await versions.create(
        skill_id=skill.id,
        version="1.0.0",
        description="d",
        keywords=[],
        raw_metadata={},
        storage_prefix=prefix,
        entry_file="SKILL.md",
        uploaded_by_user_id=None,
        content_hash="",
    )
    install = await installs.upsert(
        org_id=org_id,
        skill_id=skill.id,
        installed_version="1.0.0",
        installed_by_user_id=user_id,
    )

    bindings = WorkspaceSkillBindingRepository(db_session, org_id=org_id, workspace_id=ws_id)
    await bindings.enable(install.id)

    catalog = SkillCatalogService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )

    # Found case
    result = await catalog.find_enabled_by_name(ws_id, org_id=org_id, name=skill_name)
    assert result is not None
    assert result.name == skill_name
    assert result.version == "1.0.0"

    # Not-found case
    missing = await catalog.find_enabled_by_name(ws_id, org_id=org_id, name="nonexistent")
    assert missing is None
