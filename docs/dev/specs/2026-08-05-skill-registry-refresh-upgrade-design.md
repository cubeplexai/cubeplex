# Skill Registry Refresh & Upgrade — Design

**Date:** 2026-08-05  
**Status:** draft (spec only — no implementation code in this PR)  
**Branch / worktree:** `feat/2026-08-05-skill-registry-refresh-upgrade`  
**Related:**  
- [2026-05-27 skill discovery & install](./2026-05-27-skill-discovery-install-design.md) (OQ-6: manual "Check for update")  
- [2026-05-30 skills.sh adapter](./2026-05-30-skillssh-source-design.md)

---

## Goal

Let org admins and (where safe) workspace members **pull a newer bundle from a
remote skill registry into the org catalog without changing what any workspace
is running**, then **explicitly upgrade** an install pointer to that catalog
version when they choose.

One sentence: **Refresh updates the catalog; Upgrade updates the install.**

---

## Context — what exists today

### Provenance is stored

Remote install already writes:

- `Skill.imported_from_registry_id` → `skill_registries.id`
- `Skill.imported_from_source_ref` → e.g. `owner/repo/branch/skill-slug` (skills.sh)

`source` remains `uploaded`; registry import is provenance, not a third source type.

### Two kinds of "update" are conflated

| Concept | Meaning | Today |
|---|---|---|
| **Refresh** (from registry) | Re-fetch files via stored provenance; may append a `SkillVersion` and advance `Skill.current_version` | `POST /ws/{ws}/skills/{id}/refresh` exists but **reuses install-remote**, which **writes install rows** |
| **Upgrade install** | Point `OrgSkillInstall.installed_version` at a catalog version (usually `current_version`) | Admin UI has Upgrade + version switch; workspace UI does not surface `update_available` cleanly |

### UI gaps

- Admin: Upgrade works when `install_state === update_available`; no Check for update; detail does not show registry provenance.
- Workspace: no upgrade; merge path forces `install_state: installed`; no Check for update.
- Core has `refreshSkill` / `skillsStore.refresh` with **no component callers**.
- Docs claim "update available → Install/Update" without separating refresh vs upgrade.

### Why the current refresh is wrong for this design

`SkillInstallService._install_remote` → `SkillPublishService._publish_from_files`
always creates/upserts `OrgSkillInstall` (and on version collision still binds
install). That means "check for update" can change what agents load. Product
decision: **refresh must never touch install.**

---

## Approaches considered

### A — One-shot "update" (refresh + bump install)

One button re-fetches and moves install. Simpler UI; surprising for org-wide
(every workspace that auto-binds would jump). Rejected.

### B — Two-step: catalog-only refresh, then explicit upgrade (chosen)

1. **Refresh** — fetch remote → maybe new catalog version; **installs unchanged**.
2. **Upgrade** — operator chooses to move this scope's install pointer.

Agents keep the old installed version until Upgrade. `update_available` is the
visible gap between install and catalog.

### C — Shadow versions per workspace

Private refresh would not advance org `current_version`. Avoids "member refresh
makes org-wide cards show upgradable", but invents a second catalog and breaks
`load_skill` / storage layout. Rejected as out of proportion.

**Choice: B.** Catalog stays one org-visible `Skill` + immutable `SkillVersion`
rows; `current_version` is shared. Member private refresh may advance
`current_version` and thus show Admin "upgradable" for org-wide installs still
on the old pin — that is intentional and correct under B.

---

## Design

### 1. Product rules

#### Refresh

- Input: an existing catalog skill with non-null
  `imported_from_registry_id` + `imported_from_source_ref`.
- Action: fetch files from the registry adapter for that ref; if content is
  new, append a `SkillVersion` and set `Skill.current_version` (and description /
  keywords from frontmatter).
- **Does not** create, update, or delete any `OrgSkillInstall` or
  `WorkspaceSkillBinding`.
- Idempotent when remote content matches the latest catalog version by
  **content hash** → `changed: false`.

#### Upgrade

- Input: an install (org-wide or workspace-private) and a target catalog
  version that already exists for that skill.
- Action: set `installed_version` only.
- Admin already has this via `POST /admin/skills/{id}/install` with
  `{ version }`. Workspace-private uses existing settings install path;
  confirm upsert-by-version behavior or add a small patch if missing.

#### `update_available`

```
install exists AND install.installed_version != skill.current_version
```

Independent of how catalog advanced (refresh, re-upload, second remote
import). Not a signal that "upstream has something new we have not pulled."

### 2. Who may do what

| Install shape | Refresh | Upgrade install |
|---|---|---|
| **Org-wide** (`workspace_id IS NULL`) | **Org admin only**, Admin → Skills | **Org admin only** (existing admin install) |
| **Workspace-private** | **Workspace member** (WS Skills detail) | **Workspace member** (private install only) |
| Preinstalled / manual upload (no provenance) | No-op or clear error; no Check for update button | Version switch only if multiple versions exist (admin already) |

**Org-wide refresh is admin-only** even though it does not change installs:
advancing `current_version` is org-visible and should not be driven from a
single workspace member's click.

**Member refresh is allowed for private installs** so experimenters can pull
upstream without admin. Side effect: org catalog `current_version` may advance;
org-wide installs stay pinned until an admin Upgrades.

### 3. Detecting "content changed"

Do **not** rely only on frontmatter `version`.

1. Fetch remote files; compute the same `content_hash` used on
   `SkillVersion` (existing `compute_skill_version_hash`).
2. Compare to the hash of the skill's latest catalog version (or the version
   whose storage is current).
3. **Same hash** → `changed: false`; do not create a version row.
4. **Different hash**:
   - If frontmatter (or meta) version is **new** and unused → use it.
   - If that version string **already exists** but hash differs → **auto-assign
     the next patch** via existing `_next_version_for` (or equivalent) so
     refresh cannot get stuck on `VersionCollisionError` when upstream forgets
     to bump. Document this in the API response (`assigned_version` optional).

No migration: `content_hash` already exists on `skill_versions`.

### 4. API surface (scope-isolated)

Shared service logic; **separate handlers** (AGENTS.md).

#### Admin

```
POST /api/v1/admin/skills/{skill_id}/refresh
→ SkillRefreshResponse
```

- Auth: org admin.
- Any org-visible skill with provenance (whether org-wide-installed, private
  elsewhere, or catalog-only).
- Catalog-only; never writes install.

#### Workspace

```
POST /api/v1/ws/{workspace_id}/skills/{skill_id}/refresh
→ SkillRefreshResponse
```

- Auth: workspace member.
- Allowed only if the skill has provenance **and** there is a
  **workspace-private** install for `(org, workspace, skill)`.
- If the skill is only org-wide (or not installed here): **403/422** with a
  stable detail code, e.g. `REFRESH_ADMIN_ONLY` or `REFRESH_PRIVATE_ONLY`.
- Catalog-only; never writes install.

#### Response shape (both)

```
{
  "canonical_name": str,
  "skill_id": str,
  "current_version": str,       # catalog tip after the call
  "previous_version": str,      # catalog tip before the call
  "changed": bool,              # true iff a new SkillVersion was created
  "assigned_version": str | null  # new version string when changed; else null
}
```

Drop the misleading field name `installed_version` from the refresh response
(today's schema reuses it for catalog tip). Callers must not treat refresh as
install.

Existing upgrade endpoints stay:

- `POST /admin/skills/{id}/install` `{ "version": "..." }`
- Workspace settings install for private (verify upsert).

#### List / detail fields

- **Admin `SkillDetail`:** add `imported_from_registry_id`,
  `imported_from_registry_name` (and optionally not expose raw `source_ref` in
  UI if noisy; keep on model for refresh).
- **Admin + WS list summaries:** compute `install_state` with real
  `update_available` for the **relevant install** (org-wide for admin list;
  for WS catalog merge, use this workspace's private or org binding install
  version vs `current_version`).
- Optional convenience: `can_refresh: bool` — server-side
  `has_provenance && registry_enabled && role_allows`. UI can also derive this.

### 5. Service layer

**Design bar:** solve the real bugs with the simplest structure. Prefer a
short dedicated path over a perfect concurrency framework. Rare races that
only jitter `current_version` or leave an extra catalog version are
acceptable; silently changing installs or forking skills is not.

Introduce a dedicated `refresh_remote_skill(skill_id, ...)` (do **not** reuse
`_install_remote` / install-writing publish):

1. Load target skill by `skill_id` + provenance; resolve adapter.
2. Fetch + validate bundle.
3. Identity pin (§5.1).
4. Content-hash compare; maybe append `SkillVersion` on **that** `skill_id`
   and advance `current_version`.
5. **Never** touch install repositories.

First-time remote install stays on the existing install-writing path.
Refactor today's ws `refresh` handler onto this path; add admin twin.

#### 5.1 Identity pin — **MUST**

Today's `_publish_from_files` derives identity via frontmatter `name` +
`find_by_name`. Fine for first import; wrong for refresh.

1. Always attach new versions to the route's `skill_id`.
2. Frontmatter `name` must equal the target skill's slug
   (`org-slug:skill-slug` → `skill-slug`).
3. Mismatch → **422** `SKILL_IDENTITY_MISMATCH`; no new skill, no alternate
   skill update, no provenance rewrite.

Without this, upstream renames silently fork the catalog.

#### 5.2 Concurrency — **SHOULD** keep simple

**MUST for U1:**

- Rely on existing uniqueness `(skill_id, version)` and normal DB
  transactions.
- If frontmatter version collides but content differs → auto next patch
  (already in §3); do not 500.
- Do not invent distributed locks, single-flight services, or cross-path
  lock audits for zip upload in this feature.

**Nice-to-have (only if one-liners):**

- `SELECT … FOR UPDATE` on the skill row inside the write transaction before
  hash compare + version insert — optional hardening, not a gate for shipping.
- Concurrent double-refresh may create two versions or last-writer tip; agents
  still use `installed_version`. No special concurrent e2e required for U1.

#### 5.3 Workspace auth — **MUST** be clear, not perfect

**MUST:**

1. WS refresh allowed only when a **workspace-private** install exists for
   `(org, workspace, skill)` and the skill has provenance.
2. Check that install in the **same request / same DB session** as the catalog
   write (not a stale flag from an earlier step). Missing → refuse
   (`REFRESH_PRIVATE_ONLY`); no catalog write.
3. Admin refresh does not require a private install.

**Not required for U1:**

- `FOR UPDATE` on the install row, multi-step re-assert protocols, or proving
  absence of TOCTOU against concurrent uninstall. Window is rare; worst case
  is a catalog tip bump after the member already had legitimate private
  access at click time — same product effect as finishing refresh one second
  earlier.

### 6. UI flow

#### Admin → Skills

Detail header:

- Badge `via {registry name}` when provenance present.
- **Check for update** when `can_refresh` (or equivalent).
  - Loading state while request in flight.
  - Toast / inline: "Already up to date" | "New version vX in catalog" |
    error (registry disabled, fetch failed).
- Existing **Upgrade to v{current}** when `update_available`.
- Versions tab: keep switch-to-version (install pointer only).

Discover candidate already `in_catalog` with same remote origin: prefer
**Check for update** (or link to skill detail) over a permanently disabled
"In Catalog" with no next step.

#### Workspace → Skills

- Catalog merge: if `installed_version != current_version` →
  `update_available` (do not force `installed`).
- Card: upgradable badge when applicable.
- Detail:
  - **Private + provenance:** Check for update; if `update_available`,
    **Upgrade** (private install only).
  - **Org-enabled / org-disabled:** no Check for update; if
    `update_available`, show "A newer catalog version is available — an org
    admin must upgrade the org install" (copy i18n).
  - **via {registry}** badge (already partially present).
- Versions tab: private may switch version if API allows; org installs stay
  read-only for members.

#### i18n

`en` + `zh` for all new strings (check for update, already latest, new version,
admin-only upgrade, refresh failed, etc.).

### 7. Edge cases

| Case | Behavior |
|---|---|
| No provenance | Refresh returns `changed: false` or 422 `NOT_REMOTE`; no button |
| Registry deleted / disabled | 422 `REGISTRY_UNAVAILABLE` |
| Fetch network / 404 / invalid bundle | 422 with safe message |
| Same content hash | `changed: false` |
| Content changed, same frontmatter version | New auto patch version; `changed: true` |
| Concurrent refresh | Accept rare tip/version jitter; uniqueness prevents duplicate version strings (§5.2) |
| Upstream renames frontmatter `name` | 422 `SKILL_IDENTITY_MISMATCH`; no fork (§5.1) |
| Private install gone at write time | Refuse WS refresh; no catalog write (§5.3) |
| Member refreshes private; org-wide still on old pin | Catalog tip advances; org install shows `update_available` for admin |
| Tombstoned preinstalled | Not remote; no refresh |
| Upgrade to version that does not exist | Existing 404/422 behavior |

### 8. Docs (same PR as the code that ships the behavior)

Update (not new pages unless necessary):

- `docs/site/docs/admin/skills-management.md`
- `docs/site/docs/guides/skills/managing-skills.md`
- Matching `zh-Hans` i18n pages

State explicitly: refresh ≠ upgrade; who can do which; agents keep
`installed_version` until upgrade.

---

## Out of scope

- Automatic / background polling of registries.
- Agent-initiated refresh or upgrade (HITL).
- Content scanning, approval queue, source allowlist beyond existing trust tiers.
- Changing preinstalled seeder upgrade UX.
- Per-user personal skill scope.
- Shadow / workspace-local catalog versions.
- Reworking first-time discover-install (stays install-writing by design).

---

## Success criteria

Observable invariants:

1. After **refresh**, every `OrgSkillInstall.installed_version` for that skill
   is **unchanged**; agent `load_skill` still serves the previously installed
   version.
2. After **refresh** with new remote content, catalog has a new
   `SkillVersion` (or auto-assigned version) and `current_version` advances;
   install_state becomes `update_available` where an install exists on the old
   pin.
3. After **upgrade**, that scope's install matches the chosen version;
   subsequent runs load it.
4. Member **cannot** refresh a skill that is only org-wide in their workspace;
   admin **can** refresh any org-visible remote-imported skill.
5. Member **can** refresh a skill with a workspace-private install and
   provenance; still criterion 1 holds.
6. Admin and workspace UIs expose the two actions with correct enablement;
   docs match.
7. Refresh never attaches versions to a different `skill_id` than requested
   (name drift → error, not fork).

---

## Implementation split (one concern per PR after this design PR)

Suggested follow-ups (detail in plan):

1. **Backend** — catalog-only refresh service; fix ws route; add admin route;
   list/detail fields; hash + version collision handling; e2e.
2. **Admin UI** — provenance badge, Check for update, wire upgrade states,
   discover affordance; i18n.
3. **Workspace UI** — merge `update_available`, private refresh + upgrade,
   org-wide messaging; i18n.
4. **Docs** — site pages (can land with the PR that first exposes the UX, or
   with admin UI if that ships the primary flow).

This document is the design lock; implementation plans track tasks without
re-deciding A vs B.
