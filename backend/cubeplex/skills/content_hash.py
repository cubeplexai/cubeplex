"""Stable content hash for a SkillVersion's full file set.

Used by sync diff to detect "same version, but bytes were overwritten in
object storage" (operator accident / redeploy). Computed once at publish /
seed / import time; stored on the SkillVersion row; compared against the
sandbox-side manifest entry at sync time.
"""

from __future__ import annotations

import asyncio
import hashlib


def _compute_skill_version_hash_sync(files: dict[str, bytes]) -> str:
    """Deterministic SHA-256 over a skill version's full file set.

    Sorted-key + length-prefixed framing eliminates concatenation ambiguity:
    {a:"foo", b:"bar"} and {a:"foobar", b:""} must NOT collide.
    """
    h = hashlib.sha256()
    for rel in sorted(files):
        body = files[rel]
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(len(body).to_bytes(8, "big"))
        h.update(body)
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


async def compute_skill_version_hash(files: dict[str, bytes]) -> str:
    """Async wrapper — hashlib releases the GIL but is sync-call from
    asyncio's POV, so we hop to a worker thread to avoid blocking the event
    loop. For typical skill sizes (sub-MB to a few MB) the wall-clock cost
    is small but the cost of forgetting to to_thread is much worse than
    paying its overhead."""
    return await asyncio.to_thread(_compute_skill_version_hash_sync, files)
