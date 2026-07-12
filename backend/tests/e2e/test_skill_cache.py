"""E2E: skill cache extracts files from object storage on first miss."""

import asyncio
from pathlib import Path

import pytest

from cubeplex.objectstore import get_objectstore_client
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.storage_paths import global_skill_prefix


@pytest.mark.asyncio
async def test_cache_fetches_files_on_miss(tmp_path: Path) -> None:
    store = get_objectstore_client()
    prefix = global_skill_prefix("test-skill", "1.0.0")
    await store.upload_file(
        f"{prefix}SKILL.md",
        b"---\nname: test-skill\ndescription: x\nversion: 1.0.0\n---\n# T",
    )
    await store.upload_file(f"{prefix}scripts/run.sh", b"#!/bin/sh\necho hi\n")

    cache = SkillCache(cache_root=tmp_path)
    files = await cache.list_files("sv-test-id", storage_prefix=prefix)
    rel_paths = sorted(f[0] for f in files)
    assert rel_paths == ["SKILL.md", "scripts/run.sh"]
    assert dict(files)["SKILL.md"].startswith(b"---")
    assert dict(files)["scripts/run.sh"] == b"#!/bin/sh\necho hi\n"


@pytest.mark.asyncio
async def test_cache_concurrent_extractions_dedupe(tmp_path: Path) -> None:
    store = get_objectstore_client()
    prefix = global_skill_prefix("dedup", "1.0.0")
    await store.upload_file(
        f"{prefix}SKILL.md",
        b"---\nname: x\ndescription: y\nversion: 1\n---",
    )

    cache = SkillCache(cache_root=tmp_path)

    async def fetch() -> list[tuple[str, bytes]]:
        return await cache.list_files("sv-dedup", storage_prefix=prefix)

    results = await asyncio.gather(*(fetch() for _ in range(5)))
    for r in results:
        assert sorted(p for p, _ in r) == ["SKILL.md"]
