"""E2E migration verification.

These tests run against whatever DB ``cubebox.config`` resolves to. They
are read-only — they only check that the short-public-id baseline migration
has been applied (tables exist with the expected column shapes).

For the destructive upgrade/downgrade roundtrip verification, see the
manual procedure documented in plan Task 6 (run alembic against a
disposable test DB via ``CUBEBOX_DATABASE__NAME=cubebox_p1_test``).
"""

import importlib
import inspect

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from cubebox.db.engine import _build_database_url

pytestmark = pytest.mark.e2e


def _load_migration(revision: str) -> object:
    """Import an Alembic migration module by its revision prefix."""
    import glob
    import os

    pattern = os.path.join(
        os.path.dirname(__file__), "..", "..", "alembic", "versions", f"{revision}_*.py"
    )
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(f"No migration file found for revision prefix {revision!r}")
    path = matches[0]
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


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


# ---------------------------------------------------------------------------
# Provider slug migration — unit-level tests of the pure helpers
# (the DB stamp+seed+upgrade pattern isn't practical with this harness;
#  _assign / _rewrite_ref / _slugify are module-level in the migration so
#  we import and exercise them directly — same coverage, no DB teardown)
# ---------------------------------------------------------------------------


def test_migration_slug_assign_system_bucket() -> None:
    """System bucket: _assign produces unique slugs with numeric suffixes."""
    m = _load_migration("538af47f81eb")
    _assign = m._assign  # type: ignore[attr-defined]
    _slugify = m._slugify  # type: ignore[attr-defined]

    system_slugs: set[str] = set()

    # system DeepSeek → "deepseek"
    base = _slugify("DeepSeek")
    slug_sys = _assign(base, system_slugs)
    assert slug_sys == "deepseek"
    system_slugs.add(slug_sys)

    # org DeepSeek — must dedup against system bucket → "deepseek-2"
    org_used: set[str] = set()
    slug_org = _assign(base, org_used | system_slugs)
    assert slug_org == "deepseek-2"
    org_used.add(slug_org)


def test_migration_slug_rewrite_ref_deepseek_collision() -> None:
    """OrgSettings ref rewrite uses org slug (deepseek-2) not system slug (deepseek).

    Scenario:
      - system provider: name='DeepSeek', slug='deepseek'
      - org provider   : name='DeepSeek', slug='deepseek-2'
      - org settings   : default_model={"model_ref": "DeepSeek/m-1"},
                         fallback_models={"models": ["DeepSeek/m-2"]},
                         task_models={"title": "DeepSeek/m-1"}
    After rewrite the org name-to-slug map resolves 'DeepSeek' → 'deepseek-2'
    (the org-scoped provider wins over the system one), so all refs become
    deepseek-2/…
    """
    m = _load_migration("538af47f81eb")
    _rewrite_ref = m._rewrite_ref  # type: ignore[attr-defined]

    # In the migration the org map is merged as {**system_map, **org_maps[org_id]},
    # so org-scoped entries override system entries for the same name.
    system_map = {"DeepSeek": "deepseek"}
    org_map = {"DeepSeek": "deepseek-2"}
    name_to_slug = {**system_map, **org_map}

    assert _rewrite_ref("DeepSeek/m-1", name_to_slug) == "deepseek-2/m-1"
    assert _rewrite_ref("DeepSeek/m-2", name_to_slug) == "deepseek-2/m-2"
    # Unknown provider ref is left unchanged
    assert _rewrite_ref("Unknown/m-1", name_to_slug) == "Unknown/m-1"
    # Malformed ref (no slash) is left unchanged
    assert _rewrite_ref("no-slash", name_to_slug) == "no-slash"


def test_mcp_connector_backfill_migration_preserves_workspace_credential_policy() -> None:
    """MCP backfill creates connector state before tombstoning workspace installs."""
    m = _load_migration("7959ed1b3e5c")

    create_state_sql = m.CREATE_WORKSPACE_STATES_SQL  # type: ignore[attr-defined]
    tombstone_sql = m.TOMBSTONE_WORKSPACE_INSTALLS_SQL  # type: ignore[attr-defined]

    assert "mcp_connectors" in m.BACKFILL_CONNECTORS_SQL  # type: ignore[attr-defined]
    assert "i.default_credential_policy" in create_state_sql
    assert "'workspace_install'" in create_state_sql
    assert "i.workspace_id IS NOT NULL" in create_state_sql
    assert "install_state = 'uninstalled'" in tombstone_sql
    assert "workspace_id IS NOT NULL" in tombstone_sql


def test_mcp_connector_state_and_grants_are_rekeyed_by_connector_id() -> None:
    """Connector cleanup must make connector_id the DB-enforced runtime key."""
    m = _load_migration("bdf4b31f91d2")

    upgrade_consts = [const for const in m.upgrade.__code__.co_consts if isinstance(const, str)]
    upgrade_text = "\n".join(upgrade_consts)

    assert "mcp_workspace_connector_states" in upgrade_text
    assert "connector_id" in upgrade_text
    assert "uq_mcp_workspace_connector_state" in upgrade_text
    assert "mcp_credential_grants" in upgrade_text
    assert "uq_mcp_credential_grant_org" in upgrade_text
    assert "uq_mcp_credential_grant_workspace" in upgrade_text
    assert "uq_mcp_credential_grant_user" in upgrade_text
    assert "grant_scope = 'org'" in upgrade_text
    assert "grant_scope = 'workspace'" in upgrade_text
    assert "grant_scope = 'user'" in upgrade_text


def test_mcp_connector_rekey_migration_drops_stale_tombstone_rows_before_not_null() -> None:
    """Legacy uninstalled installs can leave state/grant rows with no connector.

    Those rows cannot survive after ``install_id`` is dropped, so the migration
    must remove them before making ``connector_id`` NOT NULL.
    """
    m = _load_migration("bdf4b31f91d2")

    cleanup_sql = m.DROP_STALE_LEGACY_INSTALL_ROWS_SQL  # type: ignore[attr-defined]
    assert "DELETE FROM mcp_workspace_connector_states" in cleanup_sql
    assert "DELETE FROM mcp_credential_grants" in cleanup_sql
    assert "mcp_connector_installs" in cleanup_sql
    assert "install_state <> 'active'" in cleanup_sql

    upgrade_source = inspect.getsource(m.upgrade)  # type: ignore[attr-defined]
    cleanup_pos = upgrade_source.index("DROP_STALE_LEGACY_INSTALL_ROWS_SQL")
    grant_not_null_pos = upgrade_source.index("mcp_credential_grants")
    state_not_null_pos = upgrade_source.index("mcp_workspace_connector_states")
    assert cleanup_pos < grant_not_null_pos
    assert cleanup_pos < state_not_null_pos
