"""E2E: skill repositories CRUD + uniqueness invariants."""

import secrets

import pytest
from sqlalchemy.exc import IntegrityError

from cubebox.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
)


def _unique(prefix: str) -> str:
    """Return a unique skill name to avoid cross-test DB collisions."""
    return f"{prefix}-{secrets.token_hex(4)}"


@pytest.mark.asyncio
async def test_create_preinstalled_skill_and_version(db_session) -> None:
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)

    name = _unique("deep-research")
    skill = await skills.create_preinstalled(
        name=name,
        description="Multi-agent research skill",
        keywords=["research"],
        current_version="1.0.0",
    )
    assert skill.id
    assert skill.source == "preinstalled"
    assert skill.owner_org_id is None

    version = await versions.create(
        skill_id=skill.id,
        version="1.0.0",
        description=skill.description,
        keywords=skill.keywords,
        raw_metadata={},
        storage_prefix=f"skills/_global/{name}/1.0.0/",
        entry_file="SKILL.md",
        uploaded_by_user_id=None,
    )
    assert version.skill_id == skill.id
    assert version.version == "1.0.0"


@pytest.mark.asyncio
async def test_skill_name_unique(db_session) -> None:
    skills = SkillRepository(db_session)
    name = _unique("git-commit")
    await skills.create_preinstalled(
        name=name,
        description="Commit helper",
        keywords=[],
        current_version="0.1.0",
    )
    with pytest.raises(IntegrityError):
        await skills.create_preinstalled(
            name=name,
            description="dup",
            keywords=[],
            current_version="0.2.0",
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_org_install_unique_per_org(db_session) -> None:
    skills = SkillRepository(db_session)
    installs = OrgSkillInstallRepository(db_session)
    skill = await skills.create_preinstalled(
        name=_unique("deep-research"),
        description="...",
        keywords=[],
        current_version="1.0.0",
    )
    org_id = f"org-{secrets.token_hex(4)}"
    await installs.upsert(
        org_id=org_id,
        skill_id=skill.id,
        installed_version="1.0.0",
        installed_by_user_id="user-1",
    )
    # Same org+skill, different version → updates the row, doesn't insert new.
    row = await installs.upsert(
        org_id=org_id,
        skill_id=skill.id,
        installed_version="1.1.0",
        installed_by_user_id="user-1",
    )
    assert row.installed_version == "1.1.0"
    rows = await installs.list_for_org(org_id)
    assert len(rows) == 1
