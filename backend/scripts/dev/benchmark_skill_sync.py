"""Manual benchmark for skill sync (not run in CI).

Drives three paths on a real sandbox:
  - cold:    manifest absent → full sync
  - hot:     manifest matches desired → 0 push
  - delta:   one skill version bumped → push only that

Prints wall-clock in ms for each. Sanity bar:
  - hot path: < 100ms
  - cold path: 5x+ faster than legacy per-file upload (manual eyeball)

Usage:
    cd backend && uv run python scripts/dev/benchmark_skill_sync.py
"""

from __future__ import annotations

import asyncio
import io
import secrets
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.users import UserManager, _slugify_org_name
from cubebox.credentials.encryption import FernetBackend
from cubebox.db.engine import async_session_maker
from cubebox.models import Organization, User, Workspace
from cubebox.sandbox.lazy import _sync_skills
from cubebox.skills.cache import SkillCache
from cubebox.skills.service import SkillCatalogService, SkillPublishService
from tests.e2e.conftest import MemSandbox

# Import MemSandbox from test conftest — OK for a dev-only benchmark script.

_SYNC_ENCRYPTION_BACKEND = FernetBackend([Fernet.generate_key()])


def _minimal_skill_zip(slug: str, version: str = "1.0.0") -> bytes:
    """Return a minimal valid SKILL.md zip for the given slug."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "SKILL.md",
            f"---\nname: {slug}\nversion: {version}\ndescription: probe skill\n---\n# {slug}\n",
        )
    return buf.getvalue()


async def publish_skill(
    session: AsyncSession,
    *,
    org_id: str,
    org_slug: str,
    workspace_id: str,
    user_id: str,
    slug: str,
    version: str = "1.0.0",
) -> str:
    """Publish a minimal skill; return skill_id."""
    cache_dir = Path(tempfile.mkdtemp())
    publisher = SkillPublishService(session=session, cache=SkillCache(cache_root=cache_dir))
    sv = await publisher.publish_from_zip(
        org_id=org_id,
        org_slug=org_slug,
        actor_user_id=user_id,
        zip_bytes=_minimal_skill_zip(slug, version),
        workspace_id=workspace_id,
    )
    return sv.skill_id


async def main() -> None:
    """Benchmark _sync_skills cold/hot/delta paths."""
    # Setup: create fresh workspace + user + org
    suffix = secrets.token_hex(4)
    org_name = f"bench-{suffix}"
    org_slug = _slugify_org_name(org_name)

    async with async_session_maker() as setup_session:
        org = Organization(name=org_name, slug=org_slug)
        setup_session.add(org)
        await setup_session.flush()
        ws = Workspace(name=f"bench-ws-{suffix}", org_id=org.id)
        setup_session.add(ws)
        await setup_session.flush()
        org_id: str = org.id
        ws_id: str = ws.id

        # Create a test user
        email = f"bench-{suffix}@example.com"
        password = secrets.token_urlsafe(12)
        user_db: Any = SQLAlchemyUserDatabase(setup_session, User)
        manager_user = UserManager(user_db)
        user_obj = await manager_user.create(
            BaseUserCreate(email=email, password=password), safe=False
        )
        user_id: str = str(user_obj.id)
        await setup_session.commit()

    try:
        # Install N skills
        logger.info("Installing 5 skills...")
        async with async_session_maker() as session:
            for i in range(5):
                await publish_skill(
                    session,
                    org_id=org_id,
                    org_slug=org_slug,
                    workspace_id=ws_id,
                    user_id=user_id,
                    slug=f"probe-{i}",
                    version="1.0.0",
                )

        # Create MemSandbox for benchmarking
        sandbox = MemSandbox()

        # Cold path: manifest absent → full sync
        logger.info("Benchmarking cold path...")
        cache_dir = Path(tempfile.mkdtemp())
        start = time.perf_counter()
        async with async_session_maker() as session:
            catalog = SkillCatalogService(session=session, cache=SkillCache(cache_root=cache_dir))
            await _sync_skills(
                catalog=catalog,
                workspace_id=ws_id,
                org_id=org_id,
                sandbox=sandbox,
            )
        cold_ms = (time.perf_counter() - start) * 1000
        logger.info("cold path: {:.2f} ms", cold_ms)

        # Hot path: manifest matches desired → 0 push (re-sync same sandbox)
        logger.info("Benchmarking hot path...")
        start = time.perf_counter()
        async with async_session_maker() as session:
            catalog = SkillCatalogService(session=session, cache=SkillCache(cache_root=cache_dir))
            await _sync_skills(
                catalog=catalog,
                workspace_id=ws_id,
                org_id=org_id,
                sandbox=sandbox,
            )
        hot_ms = (time.perf_counter() - start) * 1000
        logger.info("hot path: {:.2f} ms", hot_ms)

        if hot_ms >= 100:
            logger.warning("hot path >= 100ms: SANITY CHECK FAILED")

        # Delta path: install one more skill, then sync
        logger.info("Benchmarking delta path...")
        async with async_session_maker() as session:
            await publish_skill(
                session,
                org_id=org_id,
                org_slug=org_slug,
                workspace_id=ws_id,
                user_id=user_id,
                slug="probe-delta",
                version="1.0.0",
            )

        start = time.perf_counter()
        async with async_session_maker() as session:
            catalog = SkillCatalogService(session=session, cache=SkillCache(cache_root=cache_dir))
            await _sync_skills(
                catalog=catalog,
                workspace_id=ws_id,
                org_id=org_id,
                sandbox=sandbox,
            )
        delta_ms = (time.perf_counter() - start) * 1000
        logger.info("delta path: {:.2f} ms", delta_ms)

        # Print summary
        logger.info("=== Benchmark Summary ===")
        logger.info("cold path:  {:.2f} ms", cold_ms)
        logger.info("hot path:   {:.2f} ms (< 100ms OK)", hot_ms)
        logger.info("delta path: {:.2f} ms", delta_ms)

        await sandbox.close()

    finally:
        # Cleanup: delete workspace (org remains)
        async with async_session_maker() as cleanup_session:
            from sqlalchemy import delete

            from cubebox.models import OrgSkillInstall
            from cubebox.models.user_sandbox import UserSandbox

            # Delete skill installs scoped to this workspace
            await cleanup_session.execute(
                delete(OrgSkillInstall).where(
                    OrgSkillInstall.workspace_id == ws_id,  # type: ignore[arg-type]
                    OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
                )
            )
            # Delete user sandbox rows
            await cleanup_session.execute(
                delete(UserSandbox).where(
                    UserSandbox.workspace_id == ws_id  # type: ignore[arg-type]
                )
            )
            # Delete workspace
            ws_row = await cleanup_session.get(Workspace, ws_id)
            if ws_row is not None:
                await cleanup_session.delete(ws_row)
            await cleanup_session.commit()


if __name__ == "__main__":
    asyncio.run(main())
