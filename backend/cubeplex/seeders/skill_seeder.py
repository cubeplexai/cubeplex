"""Preinstalled-skills seeder: walks preinstalled/ → upserts global skill rows
and uploads files to skills/_global/<name>/<version>/. Multi-replica safe via
Redis named lock.

After catalog seed, reconciles org installs so **existing** orgs pick up newly
shipped preinstalled skills (bootstrap only installs at org-create time).
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError
from sqlalchemy import delete, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Organization, OrgPreinstalledTombstone, OrgSkillInstall
from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories.skill import SkillRepository, SkillVersionRepository
from cubeplex.skills.content_hash import compute_skill_version_hash
from cubeplex.skills.frontmatter import parse_skill_md
from cubeplex.skills.storage_paths import global_skill_prefix, skill_object_key

LOCK_KEY = "cubeplex:lock:skill_seeder"
# Catalog seed is usually fast; org-install reconcile can touch many orgs.
# Concurrent inserts are conflict-safe (see _reconcile_preinstalled_installs).
LOCK_TTL_SECONDS = 120


async def seed_preinstalled_skills(
    *,
    preinstalled_dir: Path,
    db_session: AsyncSession,
    redis: Redis,
) -> None:
    """Idempotently seed preinstalled skills into the global catalog.

    Also auto-installs any missing non-tombstoned preinstalled skills for
    existing orgs (so skills added after bootstrap become loadable without a
    manual admin install).

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
        await _reconcile_preinstalled_installs(db_session)
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


async def _reconcile_preinstalled_installs(db_session: AsyncSession) -> None:
    """Install missing preinstalled skills for every existing org.

    Mirrors org-bootstrap auto-install (``auto_bind=True``, system actor).
    Skips skills an org admin already uninstalled (tombstone). Does not change
    existing installs (version pins stay as-is).
    """
    skills_repo = SkillRepository(db_session)
    active = [s for s in await skills_repo.list_preinstalled() if s.deprecated_at is None]
    if not active:
        return

    orgs = list((await db_session.execute(select(Organization))).scalars().all())
    if not orgs:
        return
    org_ids = [org.id for org in orgs]

    skill_ids = [s.id for s in active]
    existing_rows = (
        (
            await db_session.execute(
                select(OrgSkillInstall).where(
                    OrgSkillInstall.skill_id.in_(skill_ids),  # type: ignore[attr-defined]
                    OrgSkillInstall.workspace_id.is_(None),  # type: ignore[union-attr]
                )
            )
        )
        .scalars()
        .all()
    )
    existing_pairs = {(row.org_id, row.skill_id) for row in existing_rows}

    tomb_rows = (
        (
            await db_session.execute(
                select(OrgPreinstalledTombstone).where(
                    OrgPreinstalledTombstone.skill_id.in_(skill_ids)  # type: ignore[attr-defined]
                )
            )
        )
        .scalars()
        .all()
    )
    tomb_pairs = {(row.org_id, row.skill_id) for row in tomb_rows}

    created = 0
    for org_id in org_ids:
        for skill in active:
            key = (org_id, skill.id)
            if key in existing_pairs or key in tomb_pairs:
                continue
            # SAVEPOINT per row: concurrent seeder / unique-index races must not
            # abort the whole reconcile batch. Re-check tombstone inside the
            # nested txn so a concurrent admin uninstall is less likely to be
            # undone (uninstall itself is a single commit for install+tombstone).
            try:
                async with db_session.begin_nested():
                    still_tomb = (
                        await db_session.execute(
                            select(OrgPreinstalledTombstone).where(
                                OrgPreinstalledTombstone.org_id == org_id,  # type: ignore[arg-type]
                                OrgPreinstalledTombstone.skill_id == skill.id,  # type: ignore[arg-type]
                            )
                        )
                    ).scalar_one_or_none()
                    if still_tomb is not None:
                        tomb_pairs.add(key)
                        continue
                    db_session.add(
                        OrgSkillInstall(
                            org_id=org_id,
                            skill_id=skill.id,
                            installed_version=skill.current_version,
                            installed_by_user_id=None,
                            auto_bind=True,
                        )
                    )
                    await db_session.flush()
            except IntegrityError:
                # Concurrent insert of the same (org, skill) org-wide install.
                existing_pairs.add(key)
                continue
            existing_pairs.add(key)
            created += 1

    # Tombstone wins: drop any org-wide install that already has a tombstone
    # (heals dual-state from concurrent uninstall vs uncommitted reconcile).
    purge = await db_session.execute(
        delete(OrgSkillInstall).where(
            OrgSkillInstall.workspace_id.is_(None),  # type: ignore[union-attr]
            OrgSkillInstall.skill_id.in_(skill_ids),  # type: ignore[attr-defined]
            exists().where(
                OrgPreinstalledTombstone.org_id == OrgSkillInstall.org_id,  # type: ignore[arg-type]
                OrgPreinstalledTombstone.skill_id == OrgSkillInstall.skill_id,  # type: ignore[arg-type]
            ),
        )
    )
    purged = int(getattr(purge, "rowcount", 0) or 0)

    if created == 0 and purged == 0:
        return

    await db_session.commit()
    logger.info(
        "Reconciled preinstalled skill installs: created {} row(s), purged {} dual-state "
        "for {} org(s)",
        created,
        purged,
        len(org_ids),
    )
