"""E2E migration verification.

These tests run against whatever DB ``cubebox.config`` resolves to. They
are read-only — they only check that the M1 migration has been applied
and that the default ``default-org`` / ``default-ws`` rows exist.

For the destructive upgrade/downgrade roundtrip verification, see the
manual procedure documented in plan Task 6 (run alembic against a
disposable test DB via ``CUBEBOX_DATABASE__NAME=cubebox_p1_test``).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from cubebox.db.engine import _build_database_url

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_default_org_and_workspace_exist_after_migration() -> None:
    engine = create_async_engine(_build_database_url())
    try:
        async with engine.connect() as conn:
            org_row = (
                await conn.execute(text("SELECT id FROM organizations WHERE id = 'default-org'"))
            ).first()
            assert org_row is not None, "default-org missing — run alembic upgrade head"
            assert org_row[0] == "default-org"

            ws_row = (
                await conn.execute(
                    text("SELECT id, org_id FROM workspaces WHERE id = 'default-ws'")
                )
            ).first()
            assert ws_row is not None, "default-ws missing — run alembic upgrade head"
            assert ws_row[0] == "default-ws"
            assert ws_row[1] == "default-org"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scope_columns_are_not_null_on_existing_tables() -> None:
    """Backfill must have populated org_id/workspace_id on every legacy row."""
    engine = create_async_engine(_build_database_url())
    try:
        async with engine.connect() as conn:
            for tbl in ("conversations", "artifacts", "artifact_versions", "user_sandboxes"):
                row = (
                    await conn.execute(
                        text(
                            f"SELECT COUNT(*) FROM {tbl} "
                            "WHERE org_id IS NULL OR workspace_id IS NULL"
                        )
                    )
                ).first()
                assert row is not None
                assert row[0] == 0, f"{tbl} has rows with NULL scope columns"
    finally:
        await engine.dispose()
