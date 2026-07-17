"""Local extraction cache for skill files fetched from object storage.

Layout: <cache_root>/<skill_version_id>/<rel_path>
Concurrent calls for the same skill_version_id deduplicate via per-key asyncio lock.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from cubeplex.objectstore import get_objectstore_client


class SkillCache:
    """Per-process extraction cache. Cheap to instantiate."""

    def __init__(self, cache_root: Path) -> None:
        self._root = cache_root
        self._locks: dict[str, asyncio.Lock] = {}

    def cache_dir(self, skill_version_id: str) -> Path:
        return self._root / skill_version_id

    def _lock_for(self, skill_version_id: str) -> asyncio.Lock:
        return self._locks.setdefault(skill_version_id, asyncio.Lock())

    async def ensure_extracted(
        self, skill_version_id: str, *, storage_prefix: str
    ) -> Path:
        """Returns local cache dir for this version. Fetches on miss."""
        target = self.cache_dir(skill_version_id)
        sentinel = target / ".extracted"

        async with self._lock_for(skill_version_id):
            if sentinel.exists():
                # Validate the sentinel: if no real files exist (stale empty cache from
                # a previous run where objectstore was empty), delete it and re-extract.
                has_files = any(
                    f for f in target.rglob("*") if f.is_file() and f.name != ".extracted"
                )
                if has_files:
                    return target
                sentinel.unlink(missing_ok=True)
                logger.warning(
                    "Skill cache: stale sentinel for {} (no files); re-extracting",
                    skill_version_id,
                )
            target.mkdir(parents=True, exist_ok=True)

            store = get_objectstore_client()
            keys = await store.list_objects(storage_prefix)
            written = 0
            for key in keys:
                rel = key[len(storage_prefix):]
                if not rel:
                    continue
                data, _ = await store.download_file(key)
                local_path = target / rel
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(data)
                written += 1

            if written == 0:
                logger.warning(
                    "Skill cache: no files found in objectstore for {} (prefix={}); "
                    "not writing sentinel so next request retries",
                    skill_version_id,
                    storage_prefix,
                )
                return target
            sentinel.write_bytes(b"")
            logger.debug(
                "Skill cache: extracted {} files for {}", written, skill_version_id
            )
        return target

    async def list_files(
        self, skill_version_id: str, *, storage_prefix: str
    ) -> list[tuple[str, bytes]]:
        """Returns [(rel_path, bytes), ...]. Fetches via cache (extracts if missing)."""
        cache_dir = await self.ensure_extracted(
            skill_version_id, storage_prefix=storage_prefix
        )
        out: list[tuple[str, bytes]] = []
        for path in cache_dir.rglob("*"):
            if not path.is_file() or path.name == ".extracted":
                continue
            rel = path.relative_to(cache_dir).as_posix()
            out.append((rel, path.read_bytes()))
        return out
