"""Backfill SkillVersion.content_hash for rows seeded before the column existed.

Idempotent. Re-extracts each version's files via SkillCache and computes a
deterministic hash. Safe to re-run; touches only rows where content_hash == ''.
Rows whose files are missing from object storage are skipped with a warning.

Usage:
    cd backend && uv run python scripts/dev/backfill_skill_version_content_hash.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.config import config as _config
from cubeplex.db.engine import async_session_maker
from cubeplex.models.skill import SkillVersion
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.content_hash import compute_skill_version_hash


async def main() -> None:
    cache_root = Path(_config.get("skills.cache_root", "skills_cache"))
    cache = SkillCache(cache_root=cache_root)

    db: AsyncSession
    async with async_session_maker() as db:
        rows = list(
            (
                await db.execute(
                    select(SkillVersion).where(
                        SkillVersion.content_hash == ""  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        logger.info("backfill: {} SkillVersion row(s) need content_hash", len(rows))

        updated = 0
        skipped: list[str] = []
        for sv in rows:
            try:
                files_list = await cache.list_files(sv.id, storage_prefix=sv.storage_prefix)
            except Exception as exc:
                logger.warning(
                    "backfill: skipping {} ({}): cannot load files — {}",
                    sv.id,
                    sv.version,
                    exc,
                )
                skipped.append(sv.id)
                continue

            if not files_list:
                logger.warning(
                    "backfill: skipping {} ({}): zero files — objectstore prefix may be stale",
                    sv.id,
                    sv.version,
                )
                skipped.append(sv.id)
                continue

            files: dict[str, bytes] = dict(files_list)
            h = await compute_skill_version_hash(files)
            await db.execute(
                update(SkillVersion)
                .where(SkillVersion.id == sv.id)  # type: ignore[arg-type]
                .values(content_hash=h)
            )
            logger.debug("backfill: {} ({}) -> {}", sv.id, sv.version, h)
            updated += 1

        await db.commit()
        logger.info("backfill: done; {} updated, {} skipped", updated, len(skipped))
        if skipped:
            logger.warning(
                "backfill: {} SkillVersion(s) could not be hashed: {}", len(skipped), skipped
            )


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, format="{time:HH:mm:ss} | {level} | {message}", level="DEBUG")
    asyncio.run(main())
