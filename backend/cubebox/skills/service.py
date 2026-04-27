"""Skill marketplace services — read path (catalog) + write path (publish)."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import OrgSkillInstall, Skill, SkillVersion, WorkspaceSkillBinding
from cubebox.objectstore import get_objectstore_client
from cubebox.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
)
from cubebox.skills.cache import SkillCache
from cubebox.skills.frontmatter import InvalidFrontmatterError, parse_skill_md  # noqa: F401
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
        """JOIN bindings → installs → skills → matching version."""
        stmt = (
            select(Skill, SkillVersion)
            .join(OrgSkillInstall, OrgSkillInstall.skill_id == Skill.id)  # type: ignore[arg-type]
            .join(
                SkillVersion,
                (SkillVersion.skill_id == Skill.id)  # type: ignore[arg-type]
                & (SkillVersion.version == OrgSkillInstall.installed_version),
            )
            .join(
                WorkspaceSkillBinding,
                WorkspaceSkillBinding.org_skill_install_id == OrgSkillInstall.id,  # type: ignore[arg-type]
            )
            .where(
                WorkspaceSkillBinding.workspace_id == workspace_id,  # type: ignore[arg-type]
                WorkspaceSkillBinding.org_id == org_id,  # type: ignore[arg-type]
                WorkspaceSkillBinding.enabled.is_(True),  # type: ignore[attr-defined]
                OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
            )
            .order_by(Skill.name)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
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
    pass


class FileTooLargeError(ValueError):
    pass


class InvalidZipPathError(ValueError):
    pass


class SkillMdMissingError(ValueError):
    pass


class SkillPublishService:
    """Write-path: extract zip → validate → upload → DB transaction."""

    def __init__(self, *, session: AsyncSession, cache: SkillCache) -> None:
        self.session = session
        self.cache = cache

    async def publish_from_zip(
        self,
        *,
        org_id: str,
        org_slug: str,
        actor_user_id: str,
        zip_bytes: bytes,
    ) -> SkillVersion:
        """Extract, validate, upload, insert. Returns the new SkillVersion."""
        files = _extract_zip(zip_bytes)
        return await self._publish_from_files(
            org_id=org_id,
            org_slug=org_slug,
            actor_user_id=actor_user_id,
            files=files,
        )

    async def _publish_from_files(
        self,
        *,
        org_id: str,
        org_slug: str,
        actor_user_id: str,
        files: dict[str, bytes],
    ) -> SkillVersion:
        if "SKILL.md" not in files:
            raise SkillMdMissingError("zip must contain SKILL.md at root")
        fm = parse_skill_md(files["SKILL.md"].decode("utf-8"))

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
                    f"version {fm.version} already exists for {canonical_name}"
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
            )
        else:
            await skills.update_current_version(
                existing_skill.id, fm.version, fm.description, fm.keywords
            )
            skill = existing_skill

        sv = await versions.create(
            skill_id=skill.id,
            version=fm.version,
            description=fm.description,
            keywords=fm.keywords,
            raw_metadata=fm.raw_metadata,
            storage_prefix=prefix,
            entry_file="SKILL.md",
            uploaded_by_user_id=actor_user_id,
        )
        await installs.upsert(
            org_id=org_id,
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
            if info.file_size > MAX_FILE_BYTES:
                raise FileTooLargeError(
                    f"{info.filename} is {info.file_size} bytes; cap is {MAX_FILE_BYTES}"
                )
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise FileTooLargeError(f"bundle exceeds total cap of {MAX_TOTAL_BYTES} bytes")
            with z.open(info) as fp:
                out[info.filename] = fp.read()
    return out
