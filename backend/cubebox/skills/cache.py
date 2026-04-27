"""Local extraction cache for skill files fetched from object storage.

Layout: <cache_root>/<skill_version_id>/<rel_path>
Concurrent calls for the same skill_version_id deduplicate via per-key asyncio lock.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from cubebox.objectstore import get_objectstore_client


class SkillCache:
    """Per-process extraction cache. Cheap to instantiate."""

    def __init__(self, cache_root: Path) -> None:
        self._root = cache_root
        self._locks: dict[str, asyncio.Lock] = {}

    def cache_dir(self, skill_version_id: str) -> Path:
        return self._root / skill_version_id

    def _lock_for(self, skill_version_id: str) -> asyncio.Lock:
        lock = self._locks.get(skill_version_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[skill_version_id] = lock
        return lock

    async def ensure_extracted(
        self, skill_version_id: str, *, storage_prefix: str
    ) -> Path:
        """Returns local cache dir for this version. Fetches on miss."""
        target = self.cache_dir(skill_version_id)
        sentinel = target / ".extracted"

        async with self._lock_for(skill_version_id):
            if sentinel.exists():
                return target
            target.mkdir(parents=True, exist_ok=True)

            store = get_objectstore_client()
            keys = await store.list_objects(storage_prefix)
            for key in keys:
                if not key.startswith(storage_prefix):
                    continue
                rel = key[len(storage_prefix):]
                if not rel:
                    continue
                data, _ = await store.download_file(key)
                local_path = target / rel
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(data)

            sentinel.write_bytes(b"")
            logger.debug(
                "Skill cache: extracted {} files for {}", len(keys), skill_version_id
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
