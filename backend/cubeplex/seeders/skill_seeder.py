"""Preinstalled-skills seeder: walks preinstalled/ → upserts global skill rows
and uploads files to skills/_global/<name>/<version>/. Multi-replica safe via
Redis named lock.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories.skill import SkillRepository, SkillVersionRepository
from cubeplex.skills.content_hash import compute_skill_version_hash
from cubeplex.skills.frontmatter import parse_skill_md
from cubeplex.skills.storage_paths import global_skill_prefix, skill_object_key

LOCK_KEY = "cubeplex:lock:skill_seeder"
LOCK_TTL_SECONDS = 60


async def seed_preinstalled_skills(
    *,
    preinstalled_dir: Path,
    db_session: AsyncSession,
    redis: Redis,
) -> None:
    """Idempotently seed preinstalled skills into the global catalog.

    Multi-replica safe: only one process holding the Redis lock runs the seed;
    others log and return.
    """
    if not preinstalled_dir.exists():
        logger.info("Preinstalled dir does not exist; skipping seed: {}", preinstalled_dir)
        return

    lock = redis.lock(LOCK_KEY, timeout=LOCK_TTL_SECONDS, blocking=False)
    acquired = await lock.acquire()
    if not acquired:
        logger.info("Skill seeder: lock held by another replica; skipping this run")
        return

    try:
        await _do_seed(preinstalled_dir, db_session)
    finally:
        try:
            await lock.release()
        except LockNotOwnedError:
            pass


async def _do_seed(preinstalled_dir: Path, db_session: AsyncSession) -> None:
    skills = SkillRepository(db_session)
    versions = SkillVersionRepository(db_session)
    store = get_objectstore_client()

    found_names: set[str] = set()

    for skill_dir in sorted(preinstalled_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md_path = skill_dir / "SKILL.md"
        if not skill_md_path.exists():
            logger.warning("Preinstalled skill {} has no SKILL.md; skipping", skill_dir.name)
            continue

        text = skill_md_path.read_text(encoding="utf-8")
        try:
            fm = parse_skill_md(text)
        except Exception as e:
            logger.error("Failed to parse {}/SKILL.md: {}", skill_dir.name, e)
            continue

        found_names.add(fm.name)

        # 1. Upsert Skill row
        skill = await skills.find_by_name(fm.name)
        if skill is None:
            skill = await skills.create_preinstalled(
                name=fm.name,
                description=fm.description,
                keywords=fm.keywords,
                current_version=fm.version,
            )
        else:
            if skill.deprecated_at is not None:
                await skills.undeprecate(skill.id)
                logger.info("Un-deprecated re-added preinstalled skill: {}", fm.name)
                skill = await skills.get(skill.id)  # refresh
                if skill is None:
                    continue
            if skill.current_version != fm.version:
                await skills.update_current_version(
                    skill.id, fm.version, fm.description, fm.keywords
                )
                skill = await skills.get(skill.id)  # refresh
                if skill is None:
                    logger.error("Skill {} disappeared during seeding; skipping", fm.name)
                    continue

        # 2. Check if this version is already in the DB.
        existing = await versions.find(skill.id, fm.version)
        prefix = global_skill_prefix(fm.name, fm.version)

        # 3. Upload all files to object storage.
        # Always check for presence even when DB row exists: objectstore data can be
        # lost (e.g. Docker restart wipes /tmp/rustfs) while the DB row survives.
        # Build files dict unconditionally — needed for content_hash even on re-upload.
        files: dict[str, bytes] = {
            file_path.relative_to(skill_dir).as_posix(): file_path.read_bytes()
            for file_path in skill_dir.rglob("*")
            if file_path.is_file()
        }
        existing_keys = await store.list_objects(prefix)
        if not existing_keys:
            for rel, data in files.items():
                key = skill_object_key(prefix, rel)
                await store.upload_file(key, data)
            if existing is not None:
                logger.info("Re-uploaded missing objectstore files for {} v{}", fm.name, fm.version)

        if existing is not None:
            continue

        # 4. Insert SkillVersion row
        content_hash = await compute_skill_version_hash(files)
        await versions.create(
            skill_id=skill.id,
            version=fm.version,
            description=fm.description,
            keywords=fm.keywords,
            raw_metadata=fm.raw_metadata,
            storage_prefix=prefix,
            entry_file="SKILL.md",
            uploaded_by_user_id=None,
            content_hash=content_hash,
        )
        logger.info("Seeded preinstalled skill {} v{}", fm.name, fm.version)

    # 5. Deprecate any preinstalled skills no longer present on disk.
    for skill in await skills.list_preinstalled():
        if skill.name not in found_names and skill.deprecated_at is None:
            await skills.deprecate(skill.id)
            logger.info("Deprecated removed preinstalled skill: {}", skill.name)
