"""E2E: SkillPublishService.publish_from_zip."""

import io
import secrets
import zipfile

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.repositories.skill import (
    OrgSkillInstallRepository,
    SkillRepository,
)
from cubebox.skills.cache import SkillCache
from cubebox.skills.service import SkillPublishService


async def _seed_org_and_user(
    db_session: AsyncSession,
    org_id: str,
    user_id: str,
) -> None:
    """Insert minimal org and user rows to satisfy FK constraints.

    Skills and OrgSkillInstall have FKs to organizations and users
    introduced in the short-id schema.
    """
    await db_session.execute(
        text(
            "INSERT INTO organizations (id, name, slug, created_at)"
            " VALUES (:id, :name, :slug, NOW()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": org_id, "name": org_id, "slug": org_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, is_active, is_superuser,"
            " is_verified, created_at, language)"
            " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": user_id, "email": f"{user_id}@skill-publish-test.local"},
    )
    await db_session.commit()


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_publish_from_zip_creates_skill_version_and_install(tmp_path, db_session) -> None:
    org_id = f"org-{secrets.token_hex(4)}"
    org_slug = f"org-{secrets.token_hex(4)}"
    skill_name = f"my-skill-{secrets.token_hex(4)}"
    await _seed_org_and_user(db_session, org_id, "user-1")
    zip_bytes = _make_zip(
        {
            "SKILL.md": f"---\nname: {skill_name}\ndescription: ms\nversion: 0.1.0\n---\n# X\n".encode(),
            "scripts/run.sh": b"#!/bin/sh\n",
        }
    )

    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    sv = await publisher.publish_from_zip(
        org_id=org_id,
        org_slug=org_slug,
        actor_user_id="user-1",
        zip_bytes=zip_bytes,
    )

    canonical = f"{org_slug}:{skill_name}"
    skill = await SkillRepository(db_session).find_by_name(canonical)
    assert skill is not None
    assert skill.source == "uploaded"
    assert skill.owner_org_id == org_id
    assert sv.version == "0.1.0"
    assert sv.storage_prefix == f"skills/{org_id}/{skill_name}/0.1.0/"

    install = await OrgSkillInstallRepository(db_session).get(org_id, skill.id)
    assert install is not None
    assert install.installed_version == "0.1.0"


@pytest.mark.asyncio
async def test_publish_version_collision_raises(tmp_path, db_session) -> None:
    from cubebox.skills.service import VersionCollisionError

    org_id = f"org-{secrets.token_hex(4)}"
    org_slug = f"org-{secrets.token_hex(4)}"
    skill_name = f"x-{secrets.token_hex(4)}"
    await _seed_org_and_user(db_session, org_id, "u")
    z = _make_zip(
        {"SKILL.md": f"---\nname: {skill_name}\ndescription: y\nversion: 1.0.0\n---\n".encode()}
    )
    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    await publisher.publish_from_zip(
        org_id=org_id, org_slug=org_slug, actor_user_id="u", zip_bytes=z
    )
    with pytest.raises(VersionCollisionError):
        await publisher.publish_from_zip(
            org_id=org_id, org_slug=org_slug, actor_user_id="u", zip_bytes=z
        )


@pytest.mark.asyncio
async def test_publish_invalid_frontmatter_raises(tmp_path, db_session) -> None:
    from cubebox.skills.frontmatter import InvalidFrontmatterError

    z = _make_zip({"SKILL.md": b"# no frontmatter\n"})
    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    with pytest.raises(InvalidFrontmatterError):
        await publisher.publish_from_zip(org_id="o", org_slug="o", actor_user_id="u", zip_bytes=z)


@pytest.mark.asyncio
async def test_publish_rejects_name_with_colon(tmp_path, db_session) -> None:
    from cubebox.skills.service import InvalidSkillNameError

    z = _make_zip({"SKILL.md": b"---\nname: foo:bar\ndescription: y\nversion: 1.0.0\n---\n"})
    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    with pytest.raises(InvalidSkillNameError):
        await publisher.publish_from_zip(org_id="o", org_slug="o", actor_user_id="u", zip_bytes=z)


@pytest.mark.asyncio
async def test_publish_rejects_oversized_file(tmp_path, db_session) -> None:
    from cubebox.skills.service import FileTooLargeError

    big = b"x" * (11 * 1024 * 1024)
    z = _make_zip(
        {
            "SKILL.md": b"---\nname: x\ndescription: y\nversion: 1.0.0\n---\n",
            "big.bin": big,
        }
    )
    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )
    with pytest.raises(FileTooLargeError):
        await publisher.publish_from_zip(org_id="o", org_slug="o", actor_user_id="u", zip_bytes=z)
