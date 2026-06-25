"""Skill marketplace services — read path (catalog) + write path (publish)."""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from loguru import logger
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import OrgSkillInstall, Skill, SkillVersion, WorkspaceSkillBinding
from cubebox.objectstore import get_objectstore_client
from cubebox.repositories.artifact import ArtifactRepository
from cubebox.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
)
from cubebox.skills.cache import SkillCache
from cubebox.skills.frontmatter import (  # noqa: F401
    InvalidFrontmatterError,
    parse_skill_md,
    peek_skill_name,
)
from cubebox.skills.content_hash import compute_skill_version_hash
from cubebox.skills.storage_paths import org_skill_prefix, skill_object_key


@dataclass(frozen=True)
class ResolvedSkill:
    """A skill enabled in a workspace, resolved to a specific version."""

    skill_id: str
    skill_version_id: str
    name: str
    description: str
    version: str
    storage_prefix: str
    entry_file: str


class SkillCatalogService:
    """Read-path service: list workspace-enabled skills, fetch SKILL.md content."""

    def __init__(self, *, session: AsyncSession, cache: SkillCache) -> None:
        self.session = session
        self.cache = cache

    async def list_enabled_for_workspace(
        self, workspace_id: str, *, org_id: str
    ) -> list[ResolvedSkill]:
        """Return skills that are effectively enabled for this workspace.

        A skill is enabled if:
        - It has an explicit WorkspaceSkillBinding with enabled=True, OR
        - auto_bind=True on the install AND no explicit binding with enabled=False exists.
        """
        explicit_disable = exists().where(
            WorkspaceSkillBinding.org_skill_install_id == OrgSkillInstall.id,  # type: ignore[arg-type]
            WorkspaceSkillBinding.workspace_id == workspace_id,  # type: ignore[arg-type]
            WorkspaceSkillBinding.org_id == org_id,  # type: ignore[arg-type]
            WorkspaceSkillBinding.enabled.is_(False),  # type: ignore[attr-defined]
        )
        explicit_enable = exists().where(
            WorkspaceSkillBinding.org_skill_install_id == OrgSkillInstall.id,  # type: ignore[arg-type]
            WorkspaceSkillBinding.workspace_id == workspace_id,  # type: ignore[arg-type]
            WorkspaceSkillBinding.org_id == org_id,  # type: ignore[arg-type]
            WorkspaceSkillBinding.enabled.is_(True),  # type: ignore[attr-defined]
        )
        stmt = (
            select(Skill, SkillVersion)
            .join(OrgSkillInstall, OrgSkillInstall.skill_id == Skill.id)  # type: ignore[arg-type]
            .join(
                SkillVersion,
                (SkillVersion.skill_id == Skill.id)  # type: ignore[arg-type]
                & (SkillVersion.version == OrgSkillInstall.installed_version),
            )
            .where(
                OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
                OrgSkillInstall.workspace_id.is_(None),  # type: ignore[union-attr]
                or_(
                    explicit_enable,
                    (OrgSkillInstall.auto_bind.is_(True) & ~explicit_disable),  # type: ignore[attr-defined]
                ),
            )
            .order_by(Skill.name)
        )
        rows = (await self.session.execute(stmt)).all()
        org_wide_results = [
            ResolvedSkill(
                skill_id=skill.id,
                skill_version_id=sv.id,
                name=skill.name,
                description=sv.description,
                version=sv.version,
                storage_prefix=sv.storage_prefix,
                entry_file=sv.entry_file,
            )
            for (skill, sv) in rows
        ]

        # Also load workspace-private installs (always enabled)
        ws_private_stmt = (
            select(Skill, SkillVersion)
            .join(OrgSkillInstall, OrgSkillInstall.skill_id == Skill.id)  # type: ignore[arg-type]
            .join(
                SkillVersion,
                (SkillVersion.skill_id == Skill.id)  # type: ignore[arg-type]
                & (SkillVersion.version == OrgSkillInstall.installed_version),
            )
            .where(
                OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
                OrgSkillInstall.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        )
        ws_private_rows = (await self.session.execute(ws_private_stmt)).all()
        ws_private_results = [
            ResolvedSkill(
                skill_id=skill.id,
                skill_version_id=sv.id,
                name=skill.name,
                description=sv.description,
                version=sv.version,
                storage_prefix=sv.storage_prefix,
                entry_file=sv.entry_file,
            )
            for (skill, sv) in ws_private_rows
        ]

        # Deduplicate by skill_id: workspace-private takes precedence over org-wide
        seen_skill_ids: set[str] = set()
        deduped: list[ResolvedSkill] = []
        # Add workspace-private first (higher precedence)
        for row in ws_private_results:
            if row.skill_id not in seen_skill_ids:
                seen_skill_ids.add(row.skill_id)
                deduped.append(row)
        # Then add org-wide if not already covered
        for row in org_wide_results:
            if row.skill_id not in seen_skill_ids:
                seen_skill_ids.add(row.skill_id)
                deduped.append(row)
        return deduped

    async def find_enabled_by_name(
        self, workspace_id: str, *, org_id: str, name: str
    ) -> ResolvedSkill | None:
        for r in await self.list_enabled_for_workspace(workspace_id, org_id=org_id):
            if r.name == name:
                return r
        return None

    async def fetch_skill_md(self, skill_version_id: str) -> str:
        """Read SKILL.md content via local cache. Never touches sandbox."""
        sv = await self.session.get(SkillVersion, skill_version_id)
        if sv is None:
            raise ValueError(f"skill_version_id not found: {skill_version_id}")
        cache_dir = await self.cache.ensure_extracted(
            sv.id, storage_prefix=sv.storage_prefix
        )
        return (cache_dir / sv.entry_file).read_text(encoding="utf-8")

    async def list_files_for_sandbox_sync(
        self, skill_version_id: str, *, storage_prefix: str
    ) -> list[tuple[str, bytes]]:
        return await self.cache.list_files(skill_version_id, storage_prefix=storage_prefix)


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
SKILL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class InvalidSkillNameError(ValueError):
    pass


class VersionCollisionError(ValueError):
    """Raised when the version being published already exists for a skill.

    Carries the canonical name and the colliding version so callers (e.g. the
    remote-install reuse path) can bind to that exact version instead of
    guessing the skill's current_version.
    """

    def __init__(self, message: str, *, canonical_name: str = "", version: str = "") -> None:
        super().__init__(message)
        self.canonical_name = canonical_name
        self.version = version


class FileTooLargeError(ValueError):
    pass


class InvalidZipPathError(ValueError):
    pass


class SkillMdMissingError(ValueError):
    pass


def validate_skill_files(files: dict[str, bytes]) -> None:
    """Enforce path-traversal + per-file + total-size limits on a skill bundle.

    Shared by the zip-upload path (_extract_zip) and the remote-import path so
    both enforce identical limits. Raises InvalidZipPathError / FileTooLargeError.
    """
    total = 0
    for rel, data in files.items():
        if rel.startswith("/") or ".." in PurePosixPath(rel).parts:
            raise InvalidZipPathError(f"invalid path in skill bundle: {rel!r}")
        if len(data) > MAX_FILE_BYTES:
            raise FileTooLargeError(
                f"{rel} is {len(data)} bytes; cap is {MAX_FILE_BYTES}"
            )
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise FileTooLargeError(
                f"bundle exceeds total cap of {MAX_TOTAL_BYTES} bytes"
            )


class SkillPublishService:
    """Write-path: extract zip → validate → upload → DB transaction."""

    def __init__(self, *, session: AsyncSession, cache: SkillCache) -> None:
        self.session = session
        self.cache = cache

    async def publish_from_artifact(
        self,
        *,
        org_id: str,
        org_slug: str,
        actor_user_id: str,
        artifact_id: str,
        workspace_id: str,
    ) -> SkillVersion:
        """Read artifact files from object storage and run the publish pipeline."""
        repo = ArtifactRepository(self.session, org_id=org_id, workspace_id=workspace_id)
        artifact = await repo.get_by_id(artifact_id)
        if artifact is None:
            raise SkillMdMissingError(f"artifact {artifact_id} not found")
        if artifact.artifact_type != "skill":
            raise SkillMdMissingError(
                f"artifact {artifact_id!r} has type {artifact.artifact_type!r}, expected 'skill'"
            )

        prefix = f"artifacts/{artifact.conversation_id}/{artifact.id}/v{artifact.version}/"
        store = get_objectstore_client()
        keys = await store.list_objects(prefix)
        files: dict[str, bytes] = {}
        for key in keys:
            rel = key[len(prefix):].lstrip("/")
            if not rel:
                continue
            data, _ = await store.download_file(key)
            files[rel] = data

        return await self._publish_from_files(
            org_id=org_id,
            org_slug=org_slug,
            actor_user_id=actor_user_id,
            files=files,
            workspace_id=workspace_id,
        )

    async def publish_from_zip(
        self,
        *,
        org_id: str,
        org_slug: str,
        actor_user_id: str,
        zip_bytes: bytes,
        workspace_id: str | None = None,
    ) -> SkillVersion:
        """Extract, validate, upload, insert. Returns the new SkillVersion.

        If ``workspace_id`` is provided, the install row is workspace-private
        (visible only to that workspace) instead of org-wide. The Skill and
        SkillVersion rows still land in the org's catalog so future workspaces
        can install the same skill if desired.
        """
        logger.info(
            "Publishing skill zip: org_id={} org_slug={} workspace_id={} bytes={}",
            org_id,
            org_slug,
            workspace_id,
            len(zip_bytes),
        )
        files = _extract_zip(zip_bytes)
        return await self._publish_from_files(
            org_id=org_id,
            org_slug=org_slug,
            actor_user_id=actor_user_id,
            files=files,
            workspace_id=workspace_id,
        )

    async def _next_version_for(self, canonical_name: str) -> str:
        """Return the next patch version for *canonical_name*, starting at 1.0.0."""
        skill = await SkillRepository(self.session).find_by_name(canonical_name)
        if skill is None:
            return "1.0.0"
        all_versions = await SkillVersionRepository(self.session).list_for_skill(skill.id)
        best = (0, 0, 0)
        for v in all_versions:
            parts = v.version.split(".")
            if len(parts) == 3:
                try:
                    triple = (int(parts[0]), int(parts[1]), int(parts[2]))
                    if triple > best:
                        best = triple
                except ValueError:
                    continue
        if best == (0, 0, 0):
            return "1.0.0"
        return f"{best[0]}.{best[1]}.{best[2] + 1}"

    async def _publish_from_files(
        self,
        *,
        org_id: str,
        org_slug: str,
        actor_user_id: str,
        files: dict[str, bytes],
        workspace_id: str | None = None,
        imported_from_registry_id: str | None = None,
        imported_from_source_ref: str | None = None,
    ) -> SkillVersion:
        if "SKILL.md" not in files:
            logger.warning(
                "Skill zip missing root SKILL.md after extraction: files={}",
                _summarize_zip_paths(files),
            )
            raise SkillMdMissingError("zip must contain SKILL.md at root")
        skill_md_text = files["SKILL.md"].decode("utf-8")

        # Use _meta.json version (clawhub format) as fallback before auto-assignment.
        meta_version: str | None = None
        if "_meta.json" in files:
            try:
                meta = json.loads(files["_meta.json"])
                v = meta.get("version") if isinstance(meta, dict) else None
                if isinstance(v, str) and v.strip():
                    meta_version = v.strip()
            except (json.JSONDecodeError, Exception):
                pass

        default_version: str | None = meta_version
        raw_name = peek_skill_name(skill_md_text)
        if raw_name is not None and default_version is None:
            canonical = f"{org_slug}:{raw_name}"
            default_version = await self._next_version_for(canonical)
        fm = parse_skill_md(skill_md_text, default_version=default_version)

        if ":" in fm.name:
            raise InvalidSkillNameError(
                "frontmatter 'name' must not contain ':'; the org prefix is added by the server"
            )
        if not SKILL_SLUG_RE.match(fm.name):
            raise InvalidSkillNameError(
                f"name must match {SKILL_SLUG_RE.pattern}; got {fm.name!r}"
            )

        canonical_name = f"{org_slug}:{fm.name}"
        skills = SkillRepository(self.session)
        versions = SkillVersionRepository(self.session)
        installs = OrgSkillInstallRepository(self.session)

        existing_skill = await skills.find_by_name(canonical_name)
        if existing_skill is not None:
            existing_version = await versions.find(existing_skill.id, fm.version)
            if existing_version is not None:
                raise VersionCollisionError(
                    f"version {fm.version} already exists for {canonical_name}",
                    canonical_name=canonical_name,
                    version=fm.version,
                )

        prefix = org_skill_prefix(org_id, fm.name, fm.version)

        # Upload all files
        store = get_objectstore_client()
        for rel, data in files.items():
            await store.upload_file(skill_object_key(prefix, rel), data)

        # Create or update Skill row
        if existing_skill is None:
            skill = await skills.create_uploaded(
                canonical_name=canonical_name,
                owner_org_id=org_id,
                description=fm.description,
                keywords=fm.keywords,
                current_version=fm.version,
                imported_from_registry_id=imported_from_registry_id,
                imported_from_source_ref=imported_from_source_ref,
            )
        else:
            await skills.update_current_version(
                existing_skill.id, fm.version, fm.description, fm.keywords
            )
            skill = existing_skill

        content_hash = await compute_skill_version_hash(files)
        sv = await versions.create(
            skill_id=skill.id,
            version=fm.version,
            description=fm.description,
            keywords=fm.keywords,
            raw_metadata=fm.raw_metadata,
            storage_prefix=prefix,
            entry_file="SKILL.md",
            uploaded_by_user_id=actor_user_id,
            content_hash=content_hash,
        )
        if workspace_id is None:
            await installs.upsert(
                org_id=org_id,
                skill_id=skill.id,
                installed_version=fm.version,
                installed_by_user_id=actor_user_id,
                auto_bind=False,  # uploaded skills opt-in; admin enables per workspace
            )
        else:
            await installs.create_for_workspace(
                org_id=org_id,
                workspace_id=workspace_id,
                skill_id=skill.id,
                installed_version=fm.version,
                installed_by_user_id=actor_user_id,
            )
        return sv


def _extract_zip(zip_bytes: bytes) -> dict[str, bytes]:
    """Extract a .zip into a {rel_path: bytes} dict, enforcing size caps."""
    out: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes), mode="r") as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            if ".." in PurePosixPath(info.filename).parts:
                raise InvalidZipPathError(f"invalid path in zip: {info.filename!r}")
            if _is_macos_metadata_path(info.filename):
                logger.info("Skipping macOS zip metadata path: {}", info.filename)
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise FileTooLargeError(
                    f"{info.filename} is {info.file_size} bytes; cap is {MAX_FILE_BYTES}"
                )
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise FileTooLargeError(f"bundle exceeds total cap of {MAX_TOTAL_BYTES} bytes")
            with z.open(info) as fp:
                out[info.filename] = fp.read()
    logger.info(
        "Extracted skill zip: file_count={} total_bytes={} files={}",
        len(out),
        total,
        _summarize_zip_paths(out),
    )
    return _normalize_skill_zip_files(out)


def _normalize_skill_zip_files(files: dict[str, bytes]) -> dict[str, bytes]:
    """Strip one enclosing directory when the bundle was zipped as a folder."""
    if "SKILL.md" in files:
        logger.info("Skill zip contains root SKILL.md; no path normalization needed")
        return files

    top_levels = {
        PurePosixPath(path).parts[0]
        for path in files
        if len(PurePosixPath(path).parts) > 1
    }
    if len(top_levels) != 1:
        logger.info(
            "Skill zip path normalization skipped: top_levels={} files={}",
            sorted(top_levels),
            _summarize_zip_paths(files),
        )
        return files

    root = next(iter(top_levels))
    root_prefix = f"{root}/"
    skill_md_path = f"{root_prefix}SKILL.md"
    if skill_md_path not in files:
        logger.info(
            "Skill zip path normalization skipped: expected {} not present; files={}",
            skill_md_path,
            _summarize_zip_paths(files),
        )
        return files

    normalized = {
        path.removeprefix(root_prefix): data
        for path, data in files.items()
        if path.startswith(root_prefix)
    }
    logger.info(
        "Skill zip path normalization applied: stripped_root={} files={}",
        root,
        _summarize_zip_paths(normalized),
    )
    return normalized


def _summarize_zip_paths(files: dict[str, bytes], *, limit: int = 50) -> list[str]:
    """Return a bounded, sorted list of zip paths for diagnostics."""
    paths = sorted(files)
    if len(paths) <= limit:
        return paths
    return [*paths[:limit], f"... {len(paths) - limit} more files"]


def _is_macos_metadata_path(path: str) -> bool:
    """Return True for Finder-created zip metadata entries."""
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    if parts[0] == "__MACOSX":
        return True
    return any(part == ".DS_Store" or part.startswith("._") for part in parts)
