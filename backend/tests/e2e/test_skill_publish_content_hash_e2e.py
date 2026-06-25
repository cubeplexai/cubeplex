"""E2E: publishing a skill writes a non-empty content_hash to SkillVersion.

If publish path stops computing or stops persisting content_hash, this fails.

Object storage is mocked at the outermost external boundary (the S3 put_object
call) because this test is about the DB invariant, not the upload path.
"""

from __future__ import annotations

import io
import secrets
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.skill import OrgSkillInstall, Skill, SkillVersion
from cubebox.skills.cache import SkillCache
from cubebox.skills.service import SkillPublishService


def _make_zip(name: str, version: str = "1.0.0") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "SKILL.md",
            f"---\nname: {name}\nversion: {version}\ndescription: test\n---\n# body\n",
        )
    return buf.getvalue()


@pytest.mark.asyncio
async def test_publish_writes_content_hash(tmp_path, db_session: AsyncSession) -> None:
    org_id = f"org-{secrets.token_hex(4)}"
    org_slug = f"org-{secrets.token_hex(4)}"
    user_id = f"user-{secrets.token_hex(4)}"
    skill_name = f"hash-probe-{secrets.token_hex(4)}"

    await db_session.execute(
        text(
            "INSERT INTO organizations (id, name, slug, created_at)"
            " VALUES (:id, :name, :slug, NOW()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": org_id, "name": org_id, "slug": org_slug},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, is_active, is_superuser,"
            " is_verified, created_at, language)"
            " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": user_id, "email": f"{user_id}@content-hash-test.local"},
    )
    await db_session.commit()

    publisher = SkillPublishService(
        session=db_session, cache=SkillCache(cache_root=tmp_path / "cache")
    )

    # Mock S3 at its external boundary — this test is about the DB invariant,
    # not the object-store upload path.
    mock_client = AsyncMock()
    mock_client.upload_file = AsyncMock(return_value=None)
    with patch("cubebox.skills.service.get_objectstore_client", return_value=mock_client):
        sv = await publisher.publish_from_zip(
            org_id=org_id,
            org_slug=org_slug,
            actor_user_id=user_id,
            zip_bytes=_make_zip(skill_name),
        )

    row = (
        await db_session.execute(select(SkillVersion).where(SkillVersion.id == sv.id))
    ).scalar_one()

    assert row.content_hash.startswith("sha256:")
    assert len(row.content_hash) == len("sha256:") + 64

    # cleanup — delete in FK order: OrgSkillInstall → SkillVersion → Skill
    install_row = (
        await db_session.execute(
            select(OrgSkillInstall).where(OrgSkillInstall.skill_id == sv.skill_id)
        )
    ).scalar_one_or_none()
    if install_row is not None:
        await db_session.delete(install_row)
    await db_session.delete(row)
    skill_row = (
        await db_session.execute(select(Skill).where(Skill.id == sv.skill_id))
    ).scalar_one_or_none()
    if skill_row is not None:
        await db_session.delete(skill_row)
    await db_session.commit()
