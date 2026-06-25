"""E2E: preinstalled skill seeder."""

import uuid
from pathlib import Path

import pytest
from redis.asyncio import Redis

from cubebox.repositories.skill import SkillRepository, SkillVersionRepository
from cubebox.seeders import seed_preinstalled_skills


def _unique_name(prefix: str) -> str:
    """Return a unique skill name to avoid DB state collisions across test runs."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _write_skill_md(dir_: Path, name: str, version: str, description: str = "x") -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: {version}\n---\n# {name}\n"
    )


@pytest.mark.asyncio
async def test_seed_creates_global_rows(tmp_path: Path, db_session, redis_client: Redis) -> None:
    name_a = _unique_name("deep-research")
    name_b = _unique_name("git-commit")
    src = tmp_path / "preinstalled"
    _write_skill_md(src / name_a, name=name_a, version="1.0.0")
    _write_skill_md(src / name_b, name=name_b, version="0.1.0")

    await seed_preinstalled_skills(preinstalled_dir=src, db_session=db_session, redis=redis_client)

    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)

    deep = await skills.find_by_name(name_a)
    assert deep is not None
    assert deep.source == "preinstalled"
    assert deep.owner_org_id is None
    assert deep.current_version == "1.0.0"

    deep_versions = await versions.list_for_skill(deep.id)
    assert len(deep_versions) == 1
    assert deep_versions[0].storage_prefix == f"skills/_global/{name_a}/1.0.0/"

    git = await skills.find_by_name(name_b)
    assert git is not None
    assert git.source == "preinstalled"
    assert git.owner_org_id is None
    assert git.current_version == "0.1.0"

    git_versions = await versions.list_for_skill(git.id)
    assert len(git_versions) == 1
    assert git_versions[0].storage_prefix == f"skills/_global/{name_b}/0.1.0/"


@pytest.mark.asyncio
async def test_seed_idempotent(tmp_path: Path, db_session, redis_client: Redis) -> None:
    skill_name = _unique_name("idempotent")
    src = tmp_path / "preinstalled"
    _write_skill_md(src / skill_name, name=skill_name, version="1.0.0")

    await seed_preinstalled_skills(preinstalled_dir=src, db_session=db_session, redis=redis_client)
    # Release lock between runs (the lock has already been released after first call)
    await seed_preinstalled_skills(preinstalled_dir=src, db_session=db_session, redis=redis_client)

    skills = SkillRepository(db_session)
    skill = await skills.find_by_name(skill_name)
    assert skill is not None
    versions = await SkillVersionRepository(db_session).list_for_skill(skill.id)
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_seed_adds_new_version_on_bump(
    tmp_path: Path, db_session, redis_client: Redis
) -> None:
    skill_name = _unique_name("version-bump")
    src = tmp_path / "preinstalled"
    _write_skill_md(src / skill_name, name=skill_name, version="1.0.0")
    await seed_preinstalled_skills(preinstalled_dir=src, db_session=db_session, redis=redis_client)

    _write_skill_md(src / skill_name, name=skill_name, version="1.1.0")
    await seed_preinstalled_skills(preinstalled_dir=src, db_session=db_session, redis=redis_client)

    skills = SkillRepository(db_session)
    skill = await skills.find_by_name(skill_name)
    assert skill is not None
    assert skill.current_version == "1.1.0"
    versions = await SkillVersionRepository(db_session).list_for_skill(skill.id)
    assert sorted(v.version for v in versions) == ["1.0.0", "1.1.0"]


@pytest.mark.asyncio
async def test_seed_redis_lock_prevents_concurrent_runs(
    tmp_path: Path, db_session, redis_client: Redis
) -> None:
    skill_name = _unique_name("lock-test")
    src = tmp_path / "preinstalled"
    _write_skill_md(src / skill_name, name=skill_name, version="1.0.0")

    # Acquire the lock manually so seeder finds it held
    holder = redis_client.lock("cubebox:lock:skill_seeder", timeout=10, blocking=False)
    acquired = await holder.acquire()
    assert acquired

    try:
        # Seeder should skip (lock is held)
        await seed_preinstalled_skills(
            preinstalled_dir=src, db_session=db_session, redis=redis_client
        )
        assert await SkillRepository(db_session).find_by_name(skill_name) is None
    finally:
        await holder.release()

    # Now seed should run
    await seed_preinstalled_skills(preinstalled_dir=src, db_session=db_session, redis=redis_client)
    assert await SkillRepository(db_session).find_by_name(skill_name) is not None


@pytest.mark.asyncio
async def test_seeder_writes_content_hash(tmp_path: Path, db_session, redis_client: Redis) -> None:
    """Every SkillVersion row created by the seeder must have a non-empty content_hash."""
    skill_name = _unique_name("hash-check")
    src = tmp_path / "preinstalled"
    _write_skill_md(src / skill_name, name=skill_name, version="1.0.0")

    await seed_preinstalled_skills(preinstalled_dir=src, db_session=db_session, redis=redis_client)

    # Confirm the seeded row itself has a sha256 hash.
    skills = SkillRepository(db_session)
    skill = await skills.find_by_name(skill_name)
    assert skill is not None
    versions = await SkillVersionRepository(db_session).list_for_skill(skill.id)
    assert len(versions) == 1
    assert versions[0].content_hash.startswith("sha256:")
