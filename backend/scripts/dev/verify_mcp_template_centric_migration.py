"""Seed pre-migration MCP shapes on a scratch DB, run the new revision, assert results.

Usage (inside the worktree backend/, venv active):
    createdb mcp_mig_scratch_91
    CUBEPLEX_DATABASE__NAME=mcp_mig_scratch_91 uv run alembic upgrade a687284a937b
    CUBEPLEX_DATABASE__NAME=mcp_mig_scratch_91 uv run python scripts/dev/verify_mcp_template_centric_migration.py seed
    CUBEPLEX_DATABASE__NAME=mcp_mig_scratch_91 uv run alembic upgrade head
    CUBEPLEX_DATABASE__NAME=mcp_mig_scratch_91 uv run python scripts/dev/verify_mcp_template_centric_migration.py check
    dropdb mcp_mig_scratch_91
"""

from __future__ import annotations

import sys
from urllib.parse import quote_plus

import sqlalchemy as sa

from cubeplex.config import config


def _engine() -> sa.engine.Engine:
    host = config.get("database.host", "localhost")
    port = config.get("database.port", 5432)
    user = config.get("database.user", "postgres")
    password = config.get("database.password", "")
    name = config.get("database.name", "cubeplex")
    encoded_password = quote_plus(password)
    url = f"postgresql+psycopg://{user}:{encoded_password}@{host}:{port}/{name}"
    return sa.create_engine(url)


def seed() -> None:
    """Insert pre-migration data: org/ws/user, global template, templated + custom connectors + grants."""
    engine = _engine()
    with engine.begin() as conn:
        # org
        conn.execute(
            sa.text(
                "INSERT INTO organizations (id, name, slug, created_at, updated_at) "
                "VALUES ('org-testmig00000001', 'Test Org', 'test-org', now(), now())"
            )
        )
        # workspace
        conn.execute(
            sa.text(
                "INSERT INTO workspaces (id, org_id, name, created_at, updated_at) "
                "VALUES ('ws-testmig000000001', 'org-testmig00000001', 'Main WS', now(), now())"
            )
        )
        # user
        conn.execute(
            sa.text(
                "INSERT INTO users (id, email, hashed_password, is_active, is_superuser, is_verified, language, created_at, updated_at) "
                "VALUES ('usr-testmig0000001', 'seed@example.com', 'x', true, false, true, 'en', now(), now())"
            )
        )
        # credential (needed for grants)
        conn.execute(
            sa.text(
                "INSERT INTO credentials (id, org_id, kind, name, value_encrypted, created_at, updated_at) "
                "VALUES ('crd-testmig0000001', 'org-testmig00000001', 'oauth_token', 'oauth-cred', '\\x00', now(), now())"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO credentials (id, org_id, kind, name, value_encrypted, created_at, updated_at) "
                "VALUES ('crd-testmig0000002', 'org-testmig00000001', 'api_key', 'static-cred', '\\x00', now(), now())"
            )
        )

        # global template (scope column doesn't exist yet in pre-migration schema)
        conn.execute(
            sa.text(
                "INSERT INTO mcp_connector_templates "
                "(id, slug, name, description, provider, server_url, transport, "
                " supported_auth_methods, default_credential_policy, "
                " static_auth_style, template_metadata, tool_citation_defaults, status, created_at, updated_at) "
                "VALUES ('mctpl-global0000001', 'global-tool', 'Global Tool', '', 'custom', "
                "  'https://global.example.com/mcp', 'streamable_http', "
                "  '[\"oauth\"]', 'org', 'bearer', '{}', '{}', 'active', now(), now())"
            )
        )

        # templated connector (template_id set, auth_method='oauth')
        conn.execute(
            sa.text(
                "INSERT INTO mcp_connectors "
                "(id, org_id, template_id, name, slug_name, server_url, server_url_hash, transport, "
                " auth_method, auth_status, default_credential_policy, oauth_client_config, "
                " static_auth_style, tools_cache, tool_citations, discovery_status, "
                " discovery_metadata, headers, status, created_at, updated_at) "
                "VALUES ('mcpco-templ00000001', 'org-testmig00000001', 'mctpl-global0000001', "
                "  'Global Tool', 'global-tool', 'https://global.example.com/mcp', 'hash1', "
                "  'streamable_http', 'oauth', 'authorized', 'org', '{}', "
                "  'bearer', '[]', '{}', 'not_run', '{}', '{}', 'active', now(), now())"
            )
        )
        # org grant for templated connector
        conn.execute(
            sa.text(
                "INSERT INTO mcp_credential_grants "
                "(id, org_id, connector_id, grant_scope, credential_id, created_at, updated_at) "
                "VALUES ('mcgrn-templ00000001', 'org-testmig00000001', 'mcpco-templ00000001', "
                "  'org', 'crd-testmig0000001', now(), now())"
            )
        )

        # custom connector (template_id IS NULL, auth_method='static')
        conn.execute(
            sa.text(
                "INSERT INTO mcp_connectors "
                "(id, org_id, template_id, name, slug_name, server_url, server_url_hash, transport, "
                " auth_method, auth_status, default_credential_policy, oauth_client_config, "
                " static_auth_style, tools_cache, tool_citations, discovery_status, "
                " discovery_metadata, headers, status, created_at, updated_at) "
                "VALUES ('mcpco-custom0000001', 'org-testmig00000001', NULL, "
                "  'My Custom Tool', 'my-custom-tool', 'https://custom.example.com/mcp', 'hash2', "
                "  'streamable_http', 'static', 'authorized', 'org', '{}', "
                "  'bearer', '[]', '{}', 'not_run', '{}', '{}', 'active', now(), now())"
            )
        )
        # workspace grant for custom connector
        conn.execute(
            sa.text(
                "INSERT INTO mcp_credential_grants "
                "(id, org_id, connector_id, grant_scope, workspace_id, credential_id, created_at, updated_at) "
                "VALUES ('mcgrn-custom0000001', 'org-testmig00000001', 'mcpco-custom0000001', "
                "  'workspace', 'ws-testmig000000001', 'crd-testmig0000002', now(), now())"
            )
        )

    print("seed: done")


def check() -> None:
    """Assert post-migration state; exits non-zero on any failure."""
    engine = _engine()
    failures: list[str] = []

    def assert_ok(label: str, cond: bool) -> None:
        status = "PASS" if cond else "FAIL"
        print(f"  [{status}] {label}")
        if not cond:
            failures.append(label)

    with engine.connect() as conn:
        # --- grants: every grant has auth_method set ---
        null_auth = conn.execute(
            sa.text("SELECT COUNT(*) FROM mcp_credential_grants WHERE auth_method IS NULL")
        ).scalar()
        assert_ok("no grants with NULL auth_method", null_auth == 0)

        # --- templated connector's org grant has auth_method='oauth' ---
        oauth_grant_method = conn.execute(
            sa.text(
                "SELECT auth_method FROM mcp_credential_grants WHERE id = 'mcgrn-templ00000001'"
            )
        ).scalar()
        assert_ok("templated connector grant auth_method='oauth'", oauth_grant_method == "oauth")

        # --- custom connector's workspace grant has auth_method='static' ---
        static_grant_method = conn.execute(
            sa.text(
                "SELECT auth_method FROM mcp_credential_grants WHERE id = 'mcgrn-custom0000001'"
            )
        ).scalar()
        assert_ok("custom connector grant auth_method='static'", static_grant_method == "static")

        # --- zero connectors with template_id IS NULL ---
        null_template = conn.execute(
            sa.text("SELECT COUNT(*) FROM mcp_connectors WHERE template_id IS NULL")
        ).scalar()
        assert_ok("zero connectors with template_id IS NULL", null_template == 0)

        # --- synthesized template: scope='org', correct org_id ---
        synth_id = (
            "mctpl-"
            + conn.execute(sa.text("SELECT substr(md5('mcpco-custom0000001'), 1, 14)")).scalar()
        )
        synth_row = conn.execute(
            sa.text(
                "SELECT scope, org_id, supported_auth_methods FROM mcp_connector_templates WHERE id = :sid"
            ),
            {"sid": synth_id},
        ).fetchone()
        assert_ok("synthesized template exists", synth_row is not None)
        if synth_row is not None:
            assert_ok("synthesized template scope='org'", synth_row[0] == "org")
            assert_ok("synthesized template org_id matches", synth_row[1] == "org-testmig00000001")
            # supported_auth_methods is json; may come back as a list or string
            import json

            methods = synth_row[2]
            if isinstance(methods, str):
                methods = json.loads(methods)
            assert_ok(
                "synthesized template supported_auth_methods=['static']", methods == ["static"]
            )

        # --- mcp_connector_templates_settings table exists and is empty ---
        settings_count = conn.execute(
            sa.text("SELECT COUNT(*) FROM mcp_connector_templates_settings")
        ).scalar()
        assert_ok("mcp_connector_templates_settings exists and is empty", settings_count == 0)

        # --- mcp_connectors has no auth_method column ---
        col_exists = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = 'mcp_connectors' AND column_name = 'auth_method'"
            )
        ).scalar()
        assert_ok("mcp_connectors has no auth_method column", col_exists == 0)

        # --- auth_status also gone ---
        auth_status_exists = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = 'mcp_connectors' AND column_name = 'auth_status'"
            )
        ).scalar()
        assert_ok("mcp_connectors has no auth_status column", auth_status_exists == 0)

        # --- scope-shape check constraint exists on templates ---
        ck_exists = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.table_constraints "
                "WHERE table_name = 'mcp_connector_templates' "
                "AND constraint_name = 'ck_mcp_connector_templates_scope_shape' "
                "AND constraint_type = 'CHECK'"
            )
        ).scalar()
        assert_ok("ck_mcp_connector_templates_scope_shape exists", ck_exists == 1)

        # --- auth_method check constraint exists on grants ---
        ck_grants = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.table_constraints "
                "WHERE table_name = 'mcp_credential_grants' "
                "AND constraint_name = 'ck_mcp_credential_grants_auth_method' "
                "AND constraint_type = 'CHECK'"
            )
        ).scalar()
        assert_ok("ck_mcp_credential_grants_auth_method exists", ck_grants == 1)

        # --- uq_mcp_connector_template_per_org index has no 'IS NOT NULL' clause ---
        idx_def = conn.execute(
            sa.text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'mcp_connectors' "
                "AND indexname = 'uq_mcp_connector_template_per_org'"
            )
        ).scalar()
        assert_ok("uq_mcp_connector_template_per_org index exists", idx_def is not None)
        if idx_def is not None:
            assert_ok(
                "uq_mcp_connector_template_per_org has no 'IS NOT NULL' clause",
                "IS NOT NULL" not in idx_def,
            )

    if failures:
        print(f"\n{len(failures)} check(s) FAILED: {failures}")
        sys.exit(1)
    else:
        print("\nall checks passed")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("seed", "check"):
        print("Usage: verify_mcp_template_centric_migration.py seed|check")
        sys.exit(1)
    if sys.argv[1] == "seed":
        seed()
    else:
        check()


if __name__ == "__main__":
    main()
