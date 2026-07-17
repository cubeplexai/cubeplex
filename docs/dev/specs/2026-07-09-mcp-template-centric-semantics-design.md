# Design: MCP Template-Centric Semantics (dissolving "install")

- **Date**: 2026-07-09
- **Status**: DRAFT — pending product review
- **Related**:
  - `docs/dev/specs/2026-07-08-mcp-connector-model-cleanup-design.md`
  - `docs/dev/specs/2026-07-08-mcp-credential-layering-design.md`
  - `backend/docs/mcp_catalog_oauth.md`

## 1. Problem

The 2026-07-08 cleanup made `mcp_connectors` the single org-owned connector
row: one row per (org, template), per-workspace enablement in
`mcp_workspace_connector_states`, credentials in `mcp_credential_grants`.
The data model is right. The product semantics on top of it are not:

1. **The admin "installed" list mixes decision-makers.** It lists every
   connector row in the org (`list_org_installs()` = `list_active()`), so a
   connector one workspace enabled by itself shows up as if the org admin
   installed it. The route docstring still claims workspace installs are
   excluded — a fossil of the deleted two-scope model.
2. **The workspace "available" list mixes two different actions.** Rows
   sourced from org connectors mean "flip a switch"; rows sourced from
   templates mean "create an org-level connector as a side effect". Same
   list, same button, different blast radius.
3. **"Install" itself is an empty concept.** Visibility is (about to be) a
   template concern, enablement lives in state rows, credentials live in
   grants. What remains of "org install" is inserting a bare connector row —
   an admin install with distribution mode `none` and a workspace/user
   credential policy produces **no observable change for anyone**. An
   operation with no observable effect does not deserve a product concept.
4. **Promote has no semantic space.** With every connector row org-level,
   "promote to org" has nothing to promote.

Root cause: the model collapsed install scopes, but the vocabulary
("installed", "available to install", "promote") still describes the old
model. Fixing the vocabulary top-down is what this spec does.

## 2. Decisions (product review, 2026-07-09)

Fixed in discussion; not re-litigated here:

1. **Delete "install" from the product vocabulary.** Users face two nouns:
   *templates* (what can be connected) and *enablement + credentials*
   (who uses it, as whom).
2. **Templates gain a visibility scope**: `global` (curated catalog), `org`,
   `workspace`. Workspaces may create their own templates. **Promote** =
   widening a template's scope `workspace → org`. No approval flow in v1
   (planned later).
3. **Connector rows become lazily-created infrastructure.** They never
   appear in UI as "an installation"; they exist to hold shared config
   (server snapshot, tools cache, OAuth client identity) and to anchor
   state rows and grants.
4. **`auth_method` moves from connectors to grants.** Each credential
   records how it was provisioned; one connector may carry OAuth and static
   grants simultaneously. The connector-level auth choice — and the
   "first actor fixes org config" hazard, and the grant-guard PATCH dance —
   all disappear.
5. **Disable ≠ purge.** Disable is a reversible suspend recorded per
   (org, template); purge is a destructive cleanup of connector + grants +
   states. These are the only two lifecycle actions.
6. **Each page is one list.** Admin: all templates visible to the org, with
   fact-based status and filters. Workspace: all templates visible to the
   workspace, with an enable toggle. No installed/available split anywhere.

Governance stance (fixed earlier in the same review): self-serve — any
workspace may enable any visible, non-disabled template. Centralized-only
mode is a possible future org policy toggle, out of scope here (§9).

## 3. Data model

### 3.1 `mcp_connector_templates` — visibility scope

New columns:

| column | type | meaning |
|---|---|---|
| `scope` | text, NOT NULL, default `'global'` | `'global' \| 'org' \| 'workspace'` |
| `org_id` | FK nullable | owner org for `org`/`workspace` scope |
| `workspace_id` | FK nullable | owner workspace for `workspace` scope |
| `created_by_user_id` | FK nullable | author of custom templates |

Check constraint mirrors the grant-scope shape rule:
`(scope='global' AND org_id IS NULL AND workspace_id IS NULL) OR
(scope='org' AND org_id IS NOT NULL AND workspace_id IS NULL) OR
(scope='workspace' AND org_id IS NOT NULL AND workspace_id IS NOT NULL)`.

- Global rows remain seeded/curated; org users never write them.
- Org admins create `org`-scope templates (this replaces the admin
  "custom install" form — the form now creates a template, nothing else).
- Workspace admins create `workspace`-scope templates (new capability;
  same form, scoped visibility).
- **Promote** = `UPDATE scope 'workspace' → 'org'` (clears `workspace_id`).
  Performed by the owning workspace admin, v1 without approval. Existing
  connector rows, states, and grants are untouched — connectors snapshot
  template fields at creation, so a scope change has zero runtime effect.
- Custom templates carry the same connection fields custom installs carry
  today (`server_url`, `transport`, `supported_auth_methods`, headers,
  static auth style). `supported_auth_methods` stays the validation source
  for grants (§3.3).

Template visibility for a workspace W in org O =
`scope='global'` ∪ `scope='org' AND org_id=O` ∪
`scope='workspace' AND workspace_id=W`.
Template visibility for org admin = all of the above with the workspace
clause widened to *any workspace in O* (governance: admins see and can
disable workspace templates; see §4).

### 3.2 `mcp_connectors` — lazy carrier

- **Dropped columns**: `auth_method`, `auth_status` (both move to grant
  semantics, §3.3).
- **`template_id` becomes NOT NULL.** Custom connectors are migrated onto
  synthesized org-scope templates (§8), killing the `template_id IS NULL`
  special case everywhere.
- **Kept**: `default_credential_policy` (org/workspace/user; state rows may
  override per workspace, unchanged), `auto_enroll_new_workspaces`,
  `headers`, `tools_cache`, `discovery_*`, `timeout`, `oauth_client_config`
  (the org's DCR client identity — created lazily on the first OAuth
  grant), `status`.
- **Lazy creation rule**: the row is created (idempotently, unique on
  `(org_id, template_id)`) by the first meaningful action — a workspace
  enabling the template, an admin distributing it, or anyone creating a
  grant. Creation copies the template snapshot fields and applies template
  defaults for `default_credential_policy`. Race-safe via the unique
  constraint + retry-on-conflict.
- The `install_scope` / `workspace_id` fossil properties and
  `list_workspace_installs()` are deleted.

### 3.3 `mcp_credential_grants` — `auth_method` per grant

- New column `auth_method` (text, NOT NULL): `'oauth' | 'static'`.
  Validated against the template's `supported_auth_methods` at creation.
- Templates whose `supported_auth_methods == ['none']` never have grants;
  effective computation short-circuits to usable (auth not required).
  The old `(auth_method, policy)` pairing validators reduce to: templates
  with auth `none` force connector policy `none`, and vice versa.
- Mixed methods on one connector are legal and expected (workspace A's
  users OAuth; workspace B pastes a service-account token).
- Runtime credential resolution branches on the **grant's** method:
  OAuth grants refresh via the token manager as today; static grants
  construct auth from the template's `static_auth_style`.
- The PATCH guard `auth_method_change_blocked_by_existing_grant` is deleted
  — the field it protected no longer exists.

### 3.4 `mcp_connector_templates_settings` — new table

Per-(org, template) settings row. Absent row = all defaults. v1 carries a
single setting: `disabled`.

| column | type |
|---|---|
| `id` | public ID, prefix `mcts` (register in `models/public_id.py`) |
| `org_id` | FK, NOT NULL |
| `template_id` | FK, NOT NULL |
| `disabled` | bool, NOT NULL, default false |
| `updated_by_user_id` | FK nullable |
| `created_at` / `updated_at` | tz-aware |

Unique on `(org_id, template_id)`. Disable/re-enable = upsert with
`disabled=true/false`. Future per-org template settings (e.g. pinning)
extend this table rather than growing new shadow tables.

Applies uniformly to all three template scopes — this is the only disable
mechanism; org/workspace-owned templates do **not** get a status flag for
this (their `status` column remains the owner's definition lifecycle,
e.g. archiving a custom template from the catalog entirely).

## 4. List composition (the invariant)

> Templates answer "what can be connected"; connector rows answer nothing
> user-visible. Every list is composed from templates only, joined with
> per-row facts.

**Admin page — one list**: all templates visible to the org (global + org
+ every workspace's, the latter for governance). Each row shows facts, not
endorsements: *N workspaces enabled* (count of `enabled=True` state rows),
*org credential* (org-scope grant exists / expired / absent), *disabled*
badge, *source* (catalog / org / workspace X), *auto-enroll* marker.

Filter chips (default = **In use**):

| chip | predicate | admin question it answers |
|---|---|---|
| In use *(default)* | connector row exists | "what am I managing" |
| Needs attention | any effective error: org grant expired, OAuth pending, discovery failed | "what is broken" — the to-do queue |
| Org credential | org-scope grant exists | "which connectors' credential lifecycle is mine" |
| Unused / All | no connector row / no filter | browsing |

Plus a source dropdown (catalog / org custom / workspace-created) and text
search. The old two-section layout returns as a *presentation-layer filter
default*, not as an ontological split.

**Workspace page — one list**: templates visible to this workspace, minus
org-disabled ones. Row state = enabled / not enabled (from the workspace's
state row); action = toggle + per-scope credential connection. The
"available install list" endpoint and its template/connector row mixing
disappear.

## 5. Action semantics

**Enable (workspace member with ws-admin rights)** — lazily create the
connector row if absent, upsert own state row
(`enabled=True, enablement_source='workspace_manual'`). Rejected when the
template is org-disabled.

**Distribute (org admin, on a template row)** — replaces the old install
wizard's two buttons with one dialog, two checkboxes (both default on):

- *Enable for existing workspaces that haven't decided* — inserts state
  rows (`admin_auto`) **only for workspaces with no state row**. Existing
  rows — including explicit `enabled=False` — are never overwritten. (This
  also retires the fan-out clobber bug found on 2026-07-09 and honors the
  "existing workspace choices are preserved" copy.)
- *Auto-enable for future workspaces* — sets
  `auto_enroll_new_workspaces=True`; new-workspace bootstrap
  (`enroll_workspace_in_org_wide_mcp`) is unchanged.

Because both effects live in one dialog, the old asymmetry (install-time
`mode='all'` fans out, later PATCH of the flag does not) is gone: the flag
toggle and the fan-out are always presented together, explicitly.

**Connect credentials** — grant creation picks `auth_method` when the
template supports more than one (UI: "Sign in with OAuth" vs "Paste
token"). Scopes (org / workspace / user) and post-grant discovery are
unchanged.

**Promote (workspace admin, on own template)** — scope `workspace → org`.
No approval in v1. Audit-logged.

**Disable / re-enable (org admin, on a template row)** — upsert the
`mcp_connector_templates_settings` row with `disabled=true/false`.
Effects: hidden from workspace lists,
**excluded from runtime** (effective computation veto, §6), new enables
rejected with `template_disabled_in_org`. State rows and grants are
preserved; re-enabling restores everything. Reversible suspend.

**Purge (org admin, danger zone)** — deletes the connector row, all its
grants, and all its state rows. The template stays in the catalog. The
confirmation dialog enumerates all three deletions. This is the only
remnant of "uninstall".

## 6. Effective computation changes

- **New rule 0**: template org-disabled ⇒ connector excluded from
  workspace lists, active-tools enumeration, and agent runtime. Admin list
  still shows the row (with the disabled badge).
- Auth gates evaluate the **grant's** `auth_method`; `pending_oauth` is
  derived from "template supports oauth and no usable grant at the
  effective scope" instead of connector `auth_status`.
- Templates with auth `none` are usable without grants (unchanged
  behavior, new derivation source).
- `derive_admin_org_effective` is re-derived from (template, connector?,
  org grant?) — same decision table, minus the connector auth fields.

## 7. API surface

**Removed**

- `POST /admin/mcp/installs` (install creation; custom branch becomes
  template creation)
- `POST /admin/mcp/installs/{id}/promote-to-org` and the frontend promote
  button on connectors
- `POST /ws/{ws}/mcp/installs`, `DELETE /ws/{ws}/mcp/installs/{id}`
- `GET /ws/{ws}/mcp/available` (folded into the single workspace list)
- `GET /admin/mcp/connectors` (replaced by the catalog list)

**Added**

- `GET /admin/mcp/catalog` — template-driven admin list with joined facts
  (enabled-workspace count, org grant status, disabled flag, connector
  facts when the row exists). Paginated.
- `POST /admin/mcp/templates` / `PATCH` / `DELETE` — org-scope custom
  templates.
- `POST /ws/{ws}/mcp/templates` — workspace-scope custom templates;
  `POST /ws/{ws}/mcp/templates/{id}/promote`.
- `PUT /admin/mcp/templates/{id}/disable`, `DELETE .../disable`.
- `POST /admin/mcp/templates/{id}/purge`.
- `GET /ws/{ws}/mcp/catalog` — the workspace single list.

**Changed**

- Grant creation endpoints (admin org grant; ws workspace/user grants)
  accept `auth_method`; OAuth-start validates the template supports oauth.
- State PATCH (`/ws/{ws}/mcp/connectors/{id}/state`) unchanged except it
  performs lazy connector creation and the disabled-template rejection.
- `refresh-discovery`, try-it invoke, and `test-connection` keep their
  shapes, addressed by connector ID surfaced on catalog rows that have one.

**Docs (same PR, per repo rule 13)**: the connector pages under
`docs/site/docs/` describing admin install/uninstall and the workspace
available list must be rewritten around catalog / enable / distribute /
disable / purge. Screenshot placeholders where captures are missing.

## 8. Migration

Project is unreleased — clean cutover, no compat shims.

1. Templates: add scope columns; backfill all existing rows to
   `scope='global'`.
2. Custom connectors (`template_id IS NULL`): synthesize one org-scope
   template per row from the connector's snapshot fields, link it, then
   set `template_id` NOT NULL.
3. Grants: backfill `auth_method` from the owning connector's
   `auth_method`.
4. Connectors: drop `auth_method`, `auth_status`.
5. Create `mcp_connector_templates_settings`; register the `mcts` prefix.
6. All via `alembic revision --autogenerate` plus a data-migration step for
   2–3; verified against a disposable DB seeded with the current shapes
   (per the TDD-for-migrations judgment in AGENTS.md).

## 9. Out of scope (recorded futures)

- Approval flow for promote (v1 promotes directly; the template-scope
  design leaves a clean insertion point).
- "Promote with credentials" (moving a workspace grant to org scope).
- Org policy toggle "workspaces may only enable admin-configured
  templates" (centralized-governance mode).
- Per-template workspace usage stats page (the admin catalog's
  enabled-count is the v1 answer).
- Template snapshot refresh ("sync connector from updated template") —
  today's snapshot-at-creation behavior is kept as-is.

## 10. Edge cases

- **Slug/name collisions**: connector slug-name uniqueness stays org-wide
  (tool namespace). Two workspace templates with colliding names surface
  the conflict at enable time with the existing `install_already_exists`
  code renamed to `connector_name_conflict`; template creation pre-checks
  and suggests renaming.
- **Lazy-creation race**: two workspaces enabling simultaneously — unique
  `(org_id, template_id)` + retry-on-conflict; both end with state rows on
  one row.
- **Disable while OAuth in flight**: callback lands after disable — grant
  is stored, connector stays runtime-excluded until re-enabled. No special
  handling.
- **Workspace template whose owner workspace is deleted**: template rows
  outlive the workspace only if promoted; workspace deletion cascades
  unpromoted `workspace`-scope templates (and purges their connectors).

## 11. Testing (business invariants)

E2E-first, per `docs/testing.md`:

1. Workspace enables a visible template → connector row lazily created,
   tools usable with a user grant; second workspace enabling reuses the
   same row.
2. Admin distribute inserts state rows only where none exist; an explicit
   `enabled=False` workspace is not resurrected.
3. Org disable: workspace list hides the template, runtime excludes its
   tools, re-enable restores prior state rows and grants intact.
4. Mixed grants: OAuth user grant and static workspace grant coexist on
   one connector; each resolves correct auth headers.
5. Purge removes connector + grants + states; template remains and can be
   enabled again from zero.
6. Promote makes a workspace template visible (and enable-able) to other
   workspaces; existing enablement unaffected.
7. Admin catalog filters: "In use" = rows with connectors; "Needs
   attention" surfaces expired org grant and failed discovery.
