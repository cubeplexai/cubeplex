"""Single source of truth for object-storage layout used by skill files."""

from __future__ import annotations


def global_skill_prefix(skill_slug: str, version: str) -> str:
    """Storage prefix for a preinstalled skill version."""
    return f"skills/_global/{skill_slug}/{version}/"


def org_skill_prefix(org_id: str, skill_slug: str, version: str) -> str:
    """Storage prefix for an org-uploaded skill version."""
    return f"skills/{org_id}/{skill_slug}/{version}/"


def skill_object_key(prefix: str, rel_path: str) -> str:
    """Compose a full object key from prefix + relative path inside the bundle."""
    rel = rel_path.lstrip("/")
    return f"{prefix}{rel}"
