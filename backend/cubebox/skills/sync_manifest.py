"""Manifest schema + helpers for the sandbox-side /workspace/.skills/manifest.json.

The manifest is the persistent source of truth for "what's been synced to this
PVC". It outlives sandbox pause/resume and kill+recreate, so a fresh sandbox
attached to the same (workspace, user) can short-circuit "already up to date"
without re-uploading anything.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from cubebox.skills.sandbox_paths import SKILLS_ROOT, safe_skill_name
from cubebox.skills.sync_diff import ResolvedLike

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_PATH = f"{SKILLS_ROOT}/manifest.json"


def build_manifest(enabled: Sequence[ResolvedLike]) -> dict[str, Any]:
    """Build a fresh manifest reflecting the given enabled set."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "synced_at": datetime.now(UTC).isoformat(),
        "skills": {
            safe_skill_name(s.name): {
                "skill_version_id": s.skill_version_id,
                "version": s.version,
                "content_hash": s.content_hash,
            }
            for s in enabled
        },
    }


def parse_manifest(raw: bytes) -> dict[str, Any]:
    """Forgiving parser — any failure mode collapses to ``{"skills": {}}``,
    which signals "treat sandbox as cold" to the diff layer."""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return {"skills": {}}
    if not isinstance(obj, dict) or "skills" not in obj:
        return {"skills": {}}
    if not isinstance(obj.get("skills"), dict):
        return {"skills": {}}
    return obj


def hash_manifest(manifest: dict[str, Any]) -> str:
    """Stable sha256 over the manifest's logical content.

    Canonical: ``json.dumps`` with sorted keys and tight separators so the
    same logical content always produces the same hash, regardless of dict
    construction order or pretty-printing options.
    """
    blob = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()
