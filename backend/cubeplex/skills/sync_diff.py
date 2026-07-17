"""Pure-function diff between sandbox manifest and desired skill set.

Drives ``_sync_skills`` — no I/O, no DB; everything passed in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from cubeplex.skills.sandbox_paths import safe_skill_name


class ResolvedLike(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def version(self) -> str: ...
    @property
    def skill_version_id(self) -> str: ...
    @property
    def content_hash(self) -> str: ...
    @property
    def storage_prefix(self) -> str: ...


@dataclass(frozen=True)
class SkillSyncDiff:
    to_push: list[ResolvedLike]
    to_remove: list[str]
    to_keep: list[str]

    def is_empty(self) -> bool:
        return not self.to_push and not self.to_remove


def compute_skill_sync_diff(
    manifest: dict[str, object], desired: Sequence[ResolvedLike]
) -> SkillSyncDiff:
    """Compute push/remove/keep partitions.

    A desired entry is in ``to_push`` if:
      - manifest has no entry for its ``safe_skill_name``, OR
      - manifest entry's version differs, OR
      - both sides carry a non-empty content_hash AND they differ

    ``desired.content_hash == ""`` (legacy SkillVersion row pre-backfill)
    disables the secondary hash check — version equality alone decides.
    This prevents infinite re-push churn when neither side has a hash yet.
    (F7 fix from code review.)

    A manifest entry is in ``to_remove`` if its key is absent from desired.
    Otherwise the desired entry is in ``to_keep``.
    """
    manifest_skills = manifest.get("skills", {}) if isinstance(manifest, dict) else {}
    if not isinstance(manifest_skills, dict):
        manifest_skills = {}
    desired_by_key: dict[str, ResolvedLike] = {safe_skill_name(s.name): s for s in desired}

    to_push: list[ResolvedLike] = []
    to_keep: list[str] = []
    for key, s in desired_by_key.items():
        cur = manifest_skills.get(key)
        if cur is None or not isinstance(cur, dict):
            to_push.append(s)
            continue
        if cur.get("version") != s.version:
            to_push.append(s)
            continue
        # Only compare hashes when BOTH sides carry a non-empty hash.
        # Either side empty → trust version equality (fall through to keep).
        manifest_hash = cur.get("content_hash", "")
        if s.content_hash and manifest_hash and s.content_hash != manifest_hash:
            to_push.append(s)
            continue
        to_keep.append(key)

    to_remove = sorted(key for key in manifest_skills if key not in desired_by_key)

    return SkillSyncDiff(to_push=to_push, to_remove=to_remove, to_keep=to_keep)
