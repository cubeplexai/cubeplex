"""E2E: SkillCatalogService.list_enabled_for_workspace + fetch_skill_md."""

import pytest

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


@pytest.mark.asyncio
async def test_list_enabled_for_workspace(tmp_path, db_session) -> None:
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)
    installs = OrgSkillInstallRepository(db_session)

    import secrets

    org_id = f"org-{secrets.token_hex(4)}"
    ws_id = f"ws-{secrets.token_hex(4)}"
    user_id = "user-1"
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


@pytest.mark.asyncio
async def test_fetch_skill_md_returns_content(tmp_path, db_session) -> None:
    import secrets

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
    )

    catalog = SkillCatalogService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    content = await catalog.fetch_skill_md(sv.id)
    assert content == "# Hello\n"


@pytest.mark.asyncio
async def test_find_enabled_by_name(tmp_path, db_session) -> None:
    import secrets

    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)
    installs = OrgSkillInstallRepository(db_session)

    org_id = f"org-{secrets.token_hex(4)}"
    ws_id = f"ws-{secrets.token_hex(4)}"
    user_id = "user-1"
    skill_name = f"find-skill-{secrets.token_hex(4)}"

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
