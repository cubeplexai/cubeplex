# MCP Four-Layer — Legacy Cleanup Inventory (Snapshot)

Snapshot taken after Tasks 1-8 landed under coexist policy. The matches
below are the legacy `Catalog` / `Override` surface still in-tree. The
chore PR that closes plan §Task 9 deletes them in this order:

1. Frontend legacy `@cubebox/core` types/helpers and any non-migrated
   `MCPCatalog*` components.
2. Backend legacy routes `mcp_catalog.py` and the legacy admin/workspace
   catalog/override path handlers, with the `app.py` mount removed.
3. Backend legacy services + repositories `services/mcp_catalog.py`,
   `repositories/mcp_catalog.py`.
4. Backend legacy SQLModel classes (`MCPServer`, `MCPCatalogConnector`,
   `WorkspaceMCPOverride`, `WorkspaceMCPCredential`, `UserMCPCredential`)
   and their `models/__init__.py` exports.
5. Alembic migration that drops the old tables.

## Snapshot

Search pattern used (legacy-only identifiers):

```
rg -l 'MCPCatalogConnector|WorkspaceMCPOverride|WorkspaceMCPCredential|UserMCPCredential|mcp_catalog|MCPCatalog\b|workspace_mcp_override|workspace_mcp_credential'
```

Total files matched: **78**.

### Counts by category

| Category | Files |
| --- | --- |
| `docs_plan_or_spec` | 13 |
| `backend_test_unit` | 11 |
| `backend_mcp_module` | 9 |
| `backend_alembic_migration` | 8 |
| `frontend_core_package` | 7 |
| `backend_test_e2e` | 7 |
| `backend_api_route_or_schema` | 7 |
| `backend_service` | 3 |
| `backend_repository` | 3 |
| `backend_model` | 3 |
| `backend_seeder` | 2 |
| `backend_stream` | 1 |
| `backend_cli` | 1 |
| `backend_docs` | 1 |
| `backend_other` (config.yaml) | 1 |
| `other` (root AGENTS.md) | 1 |

### Files by category

#### backend_alembic_migration (8)
- `backend/alembic/env.py`
- `backend/alembic/versions/09a4503eba8a_add_credential_mode_to_workspace_mcp_.py`
- `backend/alembic/versions/1984c75dab8d_add_mcp_connector_tables.py`
- `backend/alembic/versions/1991a15c011d_make_workspace_mcp_override_credential_.py`
- `backend/alembic/versions/3fcdfc800664_add_mcp_four_layer_tables.py`
- `backend/alembic/versions/94630a9e13b4_add_tool_citations_to_mcp_tables.py`
- `backend/alembic/versions/94c1f2c164da_mcp_catalog_and_overrides_drop_bindings_.py`
- `backend/alembic/versions/bd12d3efd95b_short_prefixed_public_ids_and_unified_.py`

#### backend_api_route_or_schema (7)
- `backend/cubebox/api/app.py`
- `backend/cubebox/api/routes/v1/__init__.py`
- `backend/cubebox/api/routes/v1/mcp_catalog.py`
- `backend/cubebox/api/routes/v1/mcp_oauth.py`
- `backend/cubebox/api/routes/v1/ws_mcp.py`
- `backend/cubebox/api/routes/v1/ws_settings.py`
- `backend/cubebox/api/schemas/mcp.py`

#### backend_service (3)
- `backend/cubebox/services/credential.py`
- `backend/cubebox/services/mcp_catalog.py`
- `backend/cubebox/services/mcp.py`

#### backend_repository (3)
- `backend/cubebox/repositories/__init__.py`
- `backend/cubebox/repositories/mcp_catalog.py`
- `backend/cubebox/repositories/mcp.py`

#### backend_model (3)
- `backend/cubebox/models/agent_config.py`
- `backend/cubebox/models/__init__.py`
- `backend/cubebox/models/mcp.py`

#### backend_mcp_module (9)
- `backend/cubebox/mcp/cubepi_discovery.py`
- `backend/cubebox/mcp/cubepi_runtime.py`
- `backend/cubebox/mcp/dependencies.py`
- `backend/cubebox/mcp/effective.py`
- `backend/cubebox/mcp/exceptions.py`
- `backend/cubebox/mcp/oauth/callback.py`
- `backend/cubebox/mcp/oauth/start.py`
- `backend/cubebox/mcp/oauth/token_manager.py`
- `backend/cubebox/mcp/workspace_bootstrap.py`

#### backend_seeder (2)
- `backend/cubebox/seeders/__init__.py`
- `backend/cubebox/seeders/mcp_template_seeder.py`

#### backend_stream (1)
- `backend/cubebox/streams/run_manager.py`

#### backend_cli (1)
- `backend/cubebox/cli/seed_mcp_templates.py`

#### backend_other (1)
- `backend/config.yaml`

#### backend_docs (1)
- `backend/docs/mcp_catalog_oauth.md`

#### backend_test_unit (11)
- `backend/tests/unit/mcp/test_oauth_callback.py`
- `backend/tests/unit/mcp/test_oauth_callback_route.py`
- `backend/tests/unit/mcp/test_oauth_start.py`
- `backend/tests/unit/mcp/test_oauth_start_route.py`
- `backend/tests/unit/mcp/test_oauth_token_manager.py`
- `backend/tests/unit/test_install_tool_citations.py`
- `backend/tests/unit/test_mcp_catalog_repository.py`
- `backend/tests/unit/test_mcp_catalog_service.py`
- `backend/tests/unit/test_mcp_models.py`
- `backend/tests/unit/test_mcp_repositories.py`
- `backend/tests/unit/test_mcp_service_invariants.py`

#### backend_test_e2e (7)
- `backend/tests/e2e/conftest.py`
- `backend/tests/e2e/test_mcp_auto_enroll.py`
- `backend/tests/e2e/test_mcp_catalog_override.py`
- `backend/tests/e2e/test_mcp_catalog_routes.py`
- `backend/tests/e2e/test_mcp_catalog_runtime.py`
- `backend/tests/e2e/test_mcp_tool_citations.py`
- `backend/tests/e2e/test_tool_citations_routes.py`

#### frontend_core_package (7)
- `frontend/packages/core/src/api/mcp.ts`
- `frontend/packages/core/src/api/workspace-settings.ts`
- `frontend/packages/core/src/stores/mcpStore.ts`
- `frontend/packages/core/src/stores/workspaceMcpCatalogStore.ts`
- `frontend/packages/core/src/stores/workspaceSettingsStore.ts`
- `frontend/packages/core/src/types/mcp.ts`
- `frontend/packages/core/__tests__/api/mcp.test.ts`

#### docs_plan_or_spec (13)
- `docs/superpowers/plans/2026-04-30-m1e4-vault-and-m2-mcp-connectors.md`
- `docs/superpowers/plans/2026-05-05-short-public-ids-plan.md`
- `docs/superpowers/plans/2026-05-08-mcp-catalog-oauth.md`
- `docs/superpowers/plans/2026-05-12-mcp-admin-overhaul.md`
- `docs/superpowers/plans/2026-05-14-cubepi-cleanup-followup.md`
- `docs/superpowers/plans/2026-05-15-mcp-tool-citations.md`
- `docs/superpowers/plans/2026-05-16-mcp-management-four-layer.md`
- `docs/superpowers/specs/2026-04-30-m1e4-vault-and-m2-mcp-connectors-design.md`
- `docs/superpowers/specs/2026-05-05-short-public-ids-design.md`
- `docs/superpowers/specs/2026-05-08-mcp-catalog-oauth-design.md`
- `docs/superpowers/specs/2026-05-12-mcp-admin-overhaul-design.md`
- `docs/superpowers/specs/2026-05-14-mcp-tool-citations-design.md`
- `docs/superpowers/specs/2026-05-15-mcp-management-four-layer-design.md`

#### other (1)
- `AGENTS.md`

## Notes

- Anything in this list that's referenced from migration history or
  non-migrated tests is acceptable; do not chase those.
- All eight `backend/alembic/versions/*.py` files (plus `env.py`) only
  appear here because the historical migrations *created* or *altered*
  the legacy tables. The follow-up chore PR adds **one new** migration
  that drops the legacy tables; it does **not** rewrite history.
- The 13 doc/spec/plan files under `docs/superpowers/` describe historical
  designs that already shipped — they should be left intact for archival
  reasons unless a doc explicitly supersedes them. Only this
  follow-up-cleanup notes file and the active four-layer plan should be
  updated when the chore PR lands.
- `backend/cubebox/streams/run_manager.py`, `backend/cubebox/mcp/effective.py`,
  `backend/cubebox/services/mcp.py`, and
  `backend/cubebox/repositories/mcp.py` are *not* whole-file deletions —
  they still host four-layer logic. The chore PR removes only the legacy
  branches/classes inside them.
- `backend/cubebox/models/agent_config.py` carries a legacy
  `mcp_catalog_id` column reference; the chore PR's column-drop migration
  also removes that field from the model.
- `AGENTS.md` and `backend/docs/mcp_catalog_oauth.md` contain prose
  references; update them in the chore PR doc-touch step.
