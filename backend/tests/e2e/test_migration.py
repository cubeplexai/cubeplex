"""E2E migration verification.

These tests run against whatever DB ``cubebox.config`` resolves to. They
are read-only — they only check that the short-public-id baseline migration
has been applied (tables exist with the expected column shapes).

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
async def test_short_id_schema_tables_exist() -> None:
    """Verify the short-public-id baseline migration has been applied.

    Checks that all key tables exist with VARCHAR(20) PK columns —
    the signature of the new short-id schema.
    """
    engine = create_async_engine(_build_database_url())
    try:
        async with engine.connect() as conn:
            for table in ("organizations", "workspaces", "users", "conversations"):
                row = (
                    await conn.execute(
                        text(
                            "SELECT column_name, character_maximum_length "
                            "FROM information_schema.columns "
                            "WHERE table_name = :tbl AND column_name = 'id'"
                        ),
                        {"tbl": table},
                    )
                ).first()
                assert row is not None, f"table '{table}' missing — run alembic upgrade head"
                assert row[1] == 20, f"{table}.id expected VARCHAR(20) but got VARCHAR({row[1]})"
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
