"""Catalog-only remote skill refresh.

Refresh re-fetches from stored provenance and may append a SkillVersion /
advance Skill.current_version. It never creates or updates OrgSkillInstall rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Skill, SkillVersion
from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories.skill import SkillRepository, SkillVersionRepository
from cubeplex.skills.content_hash import compute_skill_version_hash
from cubeplex.skills.frontmatter import (
    InvalidFrontmatterError,
    parse_skill_md,
    peek_skill_name,
)
from cubeplex.skills.service import (
    FileTooLargeError,
    InvalidZipPathError,
    SkillPublishService,
    validate_skill_files,
)
from cubeplex.skills.sources.registry import SkillsAdapterManager
from cubeplex.skills.storage_paths import org_skill_prefix, skill_object_key


class SkillRefreshError(Exception):
    """Controlled refresh failure (maps to HTTP 422)."""

    def __init__(self, message: str, *, code: str = "REFRESH_FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RefreshResult:
    canonical_name: str
    skill_id: str
    previous_version: str
    current_version: str
    changed: bool
    assigned_version: str | None


def _expected_slug(canonical_name: str) -> str:
    return canonical_name.split(":", 1)[-1]


async def refresh_remote_catalog(
    session: AsyncSession,
    *,
    skill: Skill,
    org_id: str,
    org_slug: str,
    actor_user_id: str,
    registry: SkillsAdapterManager,
    publisher: SkillPublishService,
) -> RefreshResult:
    """Re-fetch a remote-imported skill into the catalog only.

    Guarantees: zero OrgSkillInstall mutations.
    """
    previous = skill.current_version
    if not skill.imported_from_registry_id or not skill.imported_from_source_ref:
        return RefreshResult(
            canonical_name=skill.name,
            skill_id=skill.id,
            previous_version=previous,
            current_version=previous,
            changed=False,
            assigned_version=None,
        )

    adapter = registry.adapter_by_id(skill.imported_from_registry_id)
    if adapter is None:
        raise SkillRefreshError(
            "registry unavailable or disabled",
            code="REGISTRY_UNAVAILABLE",
        )

    try:
        files = await adapter.fetch(skill.imported_from_source_ref)
    except httpx.HTTPStatusError as e:
        raise SkillRefreshError(
            f"remote fetch failed: {e.response.status_code}",
            code="FETCH_FAILED",
        ) from e
    except (httpx.RequestError, ValueError) as e:
        raise SkillRefreshError(f"remote fetch failed: {e}", code="FETCH_FAILED") from e

    if "SKILL.md" not in files:
        raise SkillRefreshError("remote bundle has no SKILL.md", code="INVALID_BUNDLE")

    try:
        validate_skill_files(files)
    except (InvalidZipPathError, FileTooLargeError) as e:
        raise SkillRefreshError(str(e), code="INVALID_BUNDLE") from e

    skill_md_text = files["SKILL.md"].decode("utf-8")
    raw_name = peek_skill_name(skill_md_text)
    expected = _expected_slug(skill.name)
    if raw_name is None or raw_name.strip() != expected:
        raise SkillRefreshError(
            f"frontmatter name {raw_name!r} does not match skill slug {expected!r}",
            code="SKILL_IDENTITY_MISMATCH",
        )

    content_hash = await compute_skill_version_hash(files)
    versions = SkillVersionRepository(session)
    tip = await versions.find(skill.id, skill.current_version)
    if tip is not None and tip.content_hash == content_hash:
        return RefreshResult(
            canonical_name=skill.name,
            skill_id=skill.id,
            previous_version=previous,
            current_version=previous,
            changed=False,
            assigned_version=None,
        )

    # Prefer frontmatter version when free; else auto next patch.
    default_version = await publisher._next_version_for(skill.name)
    try:
        fm = parse_skill_md(skill_md_text, default_version=default_version)
    except InvalidFrontmatterError as e:
        raise SkillRefreshError(str(e), code="INVALID_BUNDLE") from e
    except UnicodeDecodeError as e:
        raise SkillRefreshError(str(e), code="INVALID_BUNDLE") from e

    if ":" in fm.name:
        raise SkillRefreshError(
            "frontmatter name must not contain ':'",
            code="SKILL_IDENTITY_MISMATCH",
        )
    if fm.name != expected:
        raise SkillRefreshError(
            f"frontmatter name {fm.name!r} does not match skill slug {expected!r}",
            code="SKILL_IDENTITY_MISMATCH",
        )

    version = fm.version
    if await versions.find(skill.id, version) is not None:
        # Same version string already exists (different hash, or race). Auto-patch.
        version = await publisher._next_version_for(skill.name)

    # Strip org prefix for storage path: skill.name is "org:slug".
    slug = expected
    prefix = org_skill_prefix(org_id, slug, version)
    store = get_objectstore_client()
    for rel, data in files.items():
        await store.upload_file(skill_object_key(prefix, rel), data)

    # Single transaction: never leave current_version pointing at a missing
    # SkillVersion (update_current_version / versions.create each commit alone).
    skills = SkillRepository(session)
    skill_row = await skills.get(skill.id)
    if skill_row is None:
        raise SkillRefreshError("skill disappeared", code="SKILL_NOT_FOUND")

    skill_row.current_version = version
    skill_row.description = fm.description
    skill_row.keywords = fm.keywords
    skill_row.updated_at = datetime.now(UTC)
    session.add(
        SkillVersion(
            skill_id=skill.id,
            version=version,
            description=fm.description,
            keywords=fm.keywords,
            raw_metadata=fm.raw_metadata,
            storage_prefix=prefix,
            entry_file="SKILL.md",
            uploaded_by_user_id=actor_user_id,
            content_hash=content_hash,
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        # Concurrent refresh won the (skill_id, version) race. If tip already
        # has our content, treat as no-op; otherwise surface a controlled error.
        await session.rollback()
        refreshed = await skills.get(skill.id)
        if refreshed is None:
            raise SkillRefreshError("skill disappeared", code="SKILL_NOT_FOUND") from None
        tip_now = await versions.find(refreshed.id, refreshed.current_version)
        if tip_now is not None and tip_now.content_hash == content_hash:
            return RefreshResult(
                canonical_name=refreshed.name,
                skill_id=refreshed.id,
                previous_version=previous,
                current_version=refreshed.current_version,
                changed=False,
                assigned_version=None,
            )
        raise SkillRefreshError(
            "concurrent catalog update; retry",
            code="REFRESH_CONFLICT",
        ) from None

    return RefreshResult(
        canonical_name=skill.name,
        skill_id=skill.id,
        previous_version=previous,
        current_version=version,
        changed=True,
        assigned_version=version,
    )
