# Skill Registry Refresh & Upgrade — Plan

**Spec:** [2026-08-05-skill-registry-refresh-upgrade-design.md](../specs/2026-08-05-skill-registry-refresh-upgrade-design.md)  
**Date:** 2026-08-05  
**Worktree:** `/home/chris/cubeplex/.worktrees/feat/2026-08-05-skill-registry-refresh-upgrade`  
**Ports (this worktree):** API `8024`, web `3024` — always `cat .worktree.env` first.

---

## Goal

Ship a two-step remote skill update path: **refresh** only advances the org
catalog; **upgrade** only moves install pointers — with admin-only org-wide
refresh and member refresh for workspace-private installs.

## Architecture

Shared service method does catalog-only re-import (fetch by
`imported_from_*` → content_hash compare → maybe new `SkillVersion` +
`current_version`). Scope-isolated HTTP handlers: admin always allowed for
org-visible remote skills; workspace only when a private install exists for
that ws. Upgrade reuses existing admin install and workspace settings install
paths. UI surfaces Check for update + Upgrade separately; docs describe the
same contract.

**Load-bearing decisions:** (1) stop routing refresh through
`_install_remote` / install-writing publish; (2) identity-pin to
`skill_id`; (3) keep concurrency/auth simple — fix real bugs, not theoretical
races (spec §5 design bar).

## Tech stack

Backend: FastAPI, SQLModel, existing skill registries adapters, pytest e2e.  
Frontend: Next.js web package, `@cubeplex/core` API helpers, SWR, next-intl.

---

## PR / unit map

Implement as sequential PRs (or stacked commits) after this design PR:

| Unit | Concern | Depends on |
|---|---|---|
| **U1** | Backend catalog-only refresh + routes + list/detail correctness | — |
| **U2** | Admin UI | U1 |
| **U3** | Workspace UI | U1 |
| **U4** | Site docs (if not already in U2/U3) | U2 at minimum |

Each unit below: files · interfaces · core logic · tests.

---

## U1 — Backend: catalog-only refresh

### Files

| Path | Change |
|---|---|
| `backend/cubeplex/skills/service.py` (or small new module e.g. `skills/refresh.py`) | Catalog-only re-import; no install repo writes |
| `backend/cubeplex/skills/discovery.py` | Stop using `_install_remote` for refresh; call new path |
| `backend/cubeplex/api/routes/v1/ws_skills.py` | Gate: private install + provenance only; new response shape |
| `backend/cubeplex/api/routes/v1/admin_skills.py` | `POST /{skill_id}/refresh`; SkillDetail provenance fields |
| `backend/cubeplex/api/schemas/skill.py` | `SkillRefreshResponse` fields; `SkillDetail` provenance |
| `backend/cubeplex/api/routes/v1/ws_skills.py` list paths | Real `update_available` where an install version is known |
| `backend/tests/e2e/` (new or extend skill discovery/install e2e) | Invariants below |

No Alembic migration expected (`content_hash` already on versions).

### Interfaces

**`SkillRefreshResponse`**

```
canonical_name: str
skill_id: str
current_version: str
previous_version: str
changed: bool
assigned_version: str | null
```

**Admin:** `POST /api/v1/admin/skills/{skill_id}/refresh` → above; 404 if not
visible; 422 if no provenance / registry unavailable / fetch or validate fails.

**Workspace:** `POST /api/v1/ws/{workspace_id}/skills/{skill_id}/refresh` →
above; **403 or 422** with detail code when not workspace-private (e.g.
`REFRESH_PRIVATE_ONLY`); same provenance/registry errors as admin.

**Service (illustrative signature):**

```
async def refresh_remote_catalog(
    *, skill_id, org_id, org_slug, actor_user_id, registry_manager
) -> RefreshResult
# RefreshResult: previous_version, current_version, changed, assigned_version
# Guarantees: zero OrgSkillInstall mutations
```

**SkillDetail (admin GET):** add  
`imported_from_registry_id: str | null`,  
`imported_from_registry_name: str | null`.

Optional: `can_refresh: bool` on detail/summary if cheap; otherwise UI derives
from provenance + role.

### Core logic

1. Load target skill by `skill_id`; require provenance.
2. **WS only:** require workspace-private install for
   `(org_id, workspace_id, skill_id)`; else `REFRESH_PRIVATE_ONLY`.
3. Resolve adapter; fetch + validate files; parse frontmatter.
4. **Identity pin:** frontmatter `name` == target slug; else
   `SKILL_IDENTITY_MISMATCH` (no fork).
5. Compute content hash; if equals tip version hash → `changed=false`.
6. Else allocate version (frontmatter if free, else next patch); upload;
   create `SkillVersion` on **this** `skill_id`; update `current_version`.
7. **Never** create/update/delete `OrgSkillInstall`.

Do **not** call `_install_remote` or install-writing `_publish_from_files`.
Keep the function short; skip cross-writer lock redesigns and concurrent-session
tests for U1.

### Tests (e2e preferred)

| Invariant | Placement |
|---|---|
| Refresh with new content: new version + tip advances; **all** installs unchanged | e2e |
| Refresh same content: `changed=false` | e2e |
| Same frontmatter version, different content: auto patch, not 500 | e2e or unit |
| Frontmatter rename → `SKILL_IDENTITY_MISMATCH`; original skill unchanged | e2e |
| Member refresh org-wide-only → refused | e2e |
| Member refresh private → allowed; install unchanged | e2e |
| Admin refresh remote skill → allowed; install unchanged | e2e |
| After refresh, old install pin → `update_available`; upgrade → installed | e2e |

---

## U2 — Admin UI

### Files

| Path | Change |
|---|---|
| `frontend/packages/core/src/types/skills.ts` | Detail provenance; refresh response type |
| `frontend/packages/core/src/api/adminSkills.ts` | `adminRefreshSkill(skillId)` |
| `frontend/packages/web/components/admin/skills/SkillDetailPanel.tsx` | via badge; Check for update; revalidate after |
| `frontend/packages/web/components/admin/skills/OrgInstallActions.tsx` | Keep Upgrade; ensure refresh does not replace it |
| `frontend/packages/web/components/admin/skills/AdminCandidateDetailPanel.tsx` | in_catalog → Check for update / open skill |
| `frontend/packages/web/messages/en.json`, `zh.json` | New copy keys under `adminSkills` |

### Interfaces

- `POST /api/v1/admin/skills/{id}/refresh` via credentials + CSRF as other
  admin mutations.
- UI state machine on detail: idle → refreshing → result (toast/inline) →
  list/detail SWR mutate → Upgrade button appears if `update_available`.

### Core logic

- Show Check for update only when skill has registry provenance (from detail
  fields).
- Refresh success with `changed=true`: message includes `assigned_version` /
  `current_version`.
- Refresh `changed=false`: "Already up to date".
- Do **not** call install endpoint from the refresh button.
- Upgrade path remains `POST .../install` with `current_version` (existing).

### Tests

| Invariant | Placement |
|---|---|
| Detail shows upgrade button when API returns `update_available` | existing e2e can stay; extend if needed |
| Check for update control present for a remote-imported skill fixture | Playwright e2e if harness allows remote skill seed; else component-level not required if e2e API covers |

Prefer backend e2e for contract; one Playwright path if cheap: install fixture
→ mock/stub not ideal — follow existing admin skills e2e patterns.

---

## U3 — Workspace UI

### Files

| Path | Change |
|---|---|
| `frontend/packages/core/src/api/skills.ts` | Align `SkillRefreshResponse` with backend |
| `frontend/packages/core/src/stores/skillsStore.ts` | `refresh` already exists; ensure response typing |
| `frontend/packages/web/hooks/useWorkspaceSkillsCatalog.ts` | Compute `update_available` from install vs current; stop forcing `installed` |
| `frontend/packages/web/components/workspace-settings/skills/WorkspaceSkillCard.tsx` | Upgradable badge |
| `frontend/packages/web/components/workspace-settings/skills/WorkspaceSkillDetail.tsx` | Private: Check for update + Upgrade; org: message only |
| `frontend/packages/web/messages/en.json`, `zh.json` | `wsSettings.skillDetail` keys |

### Interfaces

- Member refresh: existing `refreshSkill(client, wsId, skillId)`.
- Private upgrade: existing `installWorkspaceSkill(client, skillId, version)` —
  **verify** it upserts version for an existing private install; if it only
  creates, fix backend settings route in U1/U3 as a small companion change.

### Core logic

```
if private && provenance → show Check for update
if update_available && private → show Upgrade (settings install to current_version)
if update_available && org-* → show admin-only message; no upgrade button
if org-* → never show Check for update
```

Catalog merge:

```
installed_version = from settings (private or org)
if installed && installed_version != current_version → install_state = update_available
else if installed → installed
else → uninstalled / available as today
```

### Tests

| Invariant | Placement |
|---|---|
| Private refresh refused by API for org-wide is not required in UI if button hidden | e2e optional |
| Merge exposes update_available when versions differ | unit on merge helper if extracted; or e2e |

---

## U4 — Site docs

### Files

| Path | Change |
|---|---|
| `docs/site/docs/admin/skills-management.md` | Two-step; admin refresh; upgrade |
| `docs/site/docs/guides/skills/managing-skills.md` | Private refresh; org-wide admin-only |
| Matching `docs/site/i18n/zh-Hans/...` pages | Same |

### Core logic

Plain language: what happens when you click each button; who can click;
agents use installed version until upgrade.

Ship docs in the same PR as the first user-visible surface (U2 or combined
U2+U3).

---

## Spec coverage checklist

| Spec requirement | Unit |
|---|---|
| Refresh never writes install | U1 |
| Hash-based no-op / version collision auto-patch | U1 |
| Admin refresh route | U1 |
| WS refresh private-only gate | U1 |
| Response shape without install lie | U1 |
| SkillDetail provenance | U1 + U2 |
| List `update_available` correct | U1 + U3 |
| Admin Check for update + Upgrade | U2 |
| WS private refresh + upgrade; org messaging | U3 |
| Docs | U4 |
| Out of scope (polling, HITL, shadow catalog) | not planned |

---

## Self-review notes

- **Interface consistency:** one `SkillRefreshResponse`; both routes return it.
- **Simplicity bar (product):** fix catalog-only + identity pin + clear
  permissions; do not build a lock protocol for rare tip races (spec §5).
- **Risk accepted:** private refresh advances shared tip (approach B);
  rare concurrent tip jitter without formal locking.
- **Risk avoided:** reusing install-writing publish (would change agent load).
- **Codex review:** identity pin kept as MUST; elaborate FOR UPDATE / cross-writer
  lock requirements deliberately **downgraded** after impact review — not worth
  the complexity for U1.

---

## Execution order

1. Land this **spec + plan PR** (design review).  
2. Implement **U1** on the same branch or a follow-up PR; e2e green.  
3. **U2** then **U3** (or U3 first if product prioritizes member private).  
4. **U4** with the first UI PR if not already done.  
5. After implementation PR opens, ask about `/pr-codex-review-loop` — do not
   auto-start.
