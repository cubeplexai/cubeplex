# skills.sh Adapter + Skill Registries Admin — Design

**Date:** 2026-05-30
**Status:** Draft

## Problem

Two problems addressed together:

1. **skills.sh adapter**: The skill discovery system supports pluggable adapters.
   The existing `RemoteRegistryAdapter` expects a custom REST protocol
   (`/search`, `/tree/{ref}`, `/raw/{ref}/{file}`). skills.sh — the registry
   behind `npx skills` — uses a different API (`/api/search`, GitHub tree + raw
   for file fetch). A dedicated adapter is required.

2. **Admin UI gap**: There is no frontend page for admins to manage skill
   registries (add skills.sh, enable/disable, set trust tier). The backend
   CRUD API exists but is inaccessible without a UI.

3. **Naming inconsistency**: The existing codebase uses "source" for two
   different concepts — the DB-persisted registry config and the adapter
   interface. Renaming to clearer terms is included in this change.

## Goal

- Admin can manage skill registries at `admin/skill-registries` (list, add,
  enable/disable, delete).
- Admin can add a skills.sh registry (`kind = 'skills-sh'`). Once enabled,
  skill discovery fans out to skills.sh in addition to the local catalog.
- Search results appear in the workspace Skills page; install pulls the
  SKILL.md bundle from GitHub and imports it into the org catalog.
- Code uses consistent "registry / adapter" terminology throughout.

---

## Part 1 — Renaming

### Name map

| Old name | New name | Location |
|---|---|---|
| `SkillSource` (Protocol) | `SkillRegistryAdapter` | `sources/base.py` |
| `SkillSource` (SQLModel) | `SkillRegistry` | `models/skill_source.py` → `models/skill_registry.py` |
| `SkillSourceRepository` | `SkillRegistryRepository` | `repositories/skill_source.py` → `repositories/skill_registry.py` |
| `SkillSourceRegistry` (container) | `SkillsAdapterManager` | `sources/registry.py` |
| `LocalCatalogSource` | `LocalCatalogAdapter` | `sources/local.py` |
| `RemoteRegistrySource` | `RemoteRegistryAdapter` | `sources/remote.py` |
| `SkillsShSource` (new) | `SkillsShAdapter` | `sources/skills_sh.py` (new) |
| table `skill_sources` | `skill_registries` | Alembic migration |
| route `/admin/skill-sources` | `/admin/skill-registries` | `routes/v1/admin_skill_sources.py` → `admin_skill_registries.py` |

### Scope boundary

Only the items in the table above are renamed. The following are **not** changed:
- `SkillCatalogService`, `SkillPublishService`, `SkillDiscoveryService`,
  `SkillInstallService` — these operate on skills (the catalog entries), not
  registries, so "skill" remains correct.
- `SkillCandidate` — a discovery result shape, not a registry concept.
- Frontend workspace Skills page components — no registry terminology exposed there.
- Historical Alembic version files — frozen records; do not edit past migrations.

### Complete rename inventory

Every file that imports or references the old names must be updated in the same
commit as the rename, or the app will fail at import time. Grep confirms 25 files:

**Source files:**
- `cubebox/skills/sources/base.py` — Protocol rename
- `cubebox/skills/sources/local.py` — class rename
- `cubebox/skills/sources/remote.py` — class rename
- `cubebox/skills/sources/registry.py` — class rename + build() logic
- `cubebox/skills/sources/skills_sh.py` — new file (imports SkillRegistryAdapter)
- `cubebox/skills/discovery.py` — imports SkillsAdapterManager
- `cubebox/streams/run_manager.py` — imports SkillsAdapterManager
- `cubebox/models/skill_source.py` → `skill_registry.py` — model rename
- `cubebox/models/skill.py` — may reference SkillSource FK target table name
- `cubebox/models/__init__.py` — export update
- `cubebox/repositories/skill_source.py` → `skill_registry.py` — class rename
- `cubebox/api/app.py` — router import
- `cubebox/api/routes/v1/__init__.py` — router import
- `cubebox/api/routes/v1/admin_skill_sources.py` → `admin_skill_registries.py` — full rename
- `cubebox/api/routes/v1/conversations.py` — may import SkillsAdapterManager
- `cubebox/api/routes/v1/ws_skills.py` — imports SkillsAdapterManager
- `alembic/env.py` — imports model for autogenerate

**Test files:**
- `tests/e2e/conftest.py` — fixtures referencing SkillSourceRepository
- `tests/e2e/test_skill_sources_admin.py` → `test_skill_registries_admin.py` — rename + update
- `tests/e2e/test_skill_discovery_remote.py` — imports RemoteRegistrySource
- `tests/e2e/test_find_skills_tool.py` — imports skill discovery stack
- `tests/unit/test_remote_registry_source.py` → `test_remote_registry_adapter.py` — rename
- `tests/unit/test_skill_discovery_ranking.py` — imports from discovery module
- `tests/unit/test_skills_sh_adapter.py` — new file

### DB migration

`skill_sources` → `skill_registries`: one `ALTER TABLE RENAME` in a new
Alembic revision. All foreign keys and indexes follow automatically in
Postgres. No data migration needed — rows are compatible as-is.

---

## Part 2 — skills.sh Adapter

### skills.sh API (observed from `npx skills` v1.5.9)

**Search:**
```
GET https://skills.sh/api/search?q={query}&limit={n}
→ {"skills": [{"name": "frontend-design", "id": "frontend-design",
               "source": "vercel-labs/skills", "installs": 1200}]}
```

**File fetch:** Files live in the skill's GitHub repo, accessed via:
- Tree: `GET https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1`
- Content: `GET https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}`

The skill lives at `{skill_slug}/` inside the repo
(e.g. `vercel-labs/skills` → `frontend-design/SKILL.md`).

### `source_ref` encoding

```
{owner}/{repo}/{branch}/{skill_slug}
e.g.  vercel-labs/skills/main/frontend-design
```

Branch is resolved at **search time**: `SkillsShAdapter.search()` makes one
`GET /repos/{owner}/{repo}` call per distinct repo in the result set to read
`default_branch`, then encodes it into each candidate's `source_ref`. This
pins installs to the branch that existed at discovery time.

`split("/", 3)` unambiguously yields `(owner, repo, branch, slug)` — only
`slug` follows the third `/`, and slugs never contain `/`.

### New class: `SkillsShAdapter`

File: `backend/cubebox/skills/sources/skills_sh.py`

```
class SkillsShAdapter:
    kind: SourceKind = "remote"   # keeps SkillsAdapterManager.adapter_by_id() routing intact

    __init__(source_id, trust_tier, source_name, github_token: str | None)

    async search(query, *, limit) -> list[SkillCandidate]
        GET https://skills.sh/api/search?q=...&limit=...
        Collect distinct {owner}/{repo} from results
        For each unique repo: GET https://api.github.com/repos/{owner}/{repo}
          → default_branch (cached within this search call only)
        For each result:
          source_ref = f"{skill['source']}/{branch}/{skill['id']}"
          candidate_id = encode_candidate_id("remote", source_ref, source_id=self.source_id)
          repo = f"https://github.com/{skill['source']}"
        Return [] silently on non-200 from skills.sh (fan-out continues)

    async fetch(source_ref) -> dict[str, bytes]
        owner, repo, branch, slug = source_ref.split("/", 3)
        GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1
        Filter entries starting with "{slug}/"
        Strip "{slug}/" prefix → relative path key
        GET raw.githubusercontent.com/{owner}/{repo}/{branch}/{slug}/{rel}
        Raise ValueError on HTTP errors, missing SKILL.md, or size violations
```

`SkillsShAdapter` is **not** a subclass of `RemoteRegistryAdapter`; it
implements `SkillRegistryAdapter` directly.

Size caps redeclared locally (`_RAW_FILE_MAX_BYTES = 10 MB`,
`_BUNDLE_MAX_BYTES = 50 MB`) — importing private names from `remote.py`
would create hidden coupling.

### `SkillsAdapterManager.build()` routing

```python
for row in rows:
    if row.kind == "skills-sh":
        adapters.append(SkillsShAdapter(
            source_id=row.id,
            trust_tier=TrustTier(row.trust_tier),
            source_name=row.name,
            github_token=settings.registry.skills_sh.github_token or None,
        ))
    else:  # "remote"
        adapters.append(RemoteRegistryAdapter(...))
```

`adapter_by_id()` (renamed from `remote_source_by_id`) matches on
`adapter.kind == "remote"` and `adapter.source_id`. Since
`SkillsShAdapter.kind = "remote"`, no change needed to the lookup logic.

### Config

```yaml
# config.yaml — under default:
registry:
  skills_sh:
    github_token: ""   # optional; raises GitHub API rate limit 60 → 5000 req/h
```

Underscore key avoids dynaconf hyphen-access ambiguity. Access in Python:
`settings.registry.skills_sh.github_token`.

Override via env: `CUBEBOX_REGISTRY__SKILLS_SH__GITHUB_TOKEN`.

### Admin API changes

`CreateSkillRegistryRequest` (renamed from `CreateSkillSourceRequest`):

```python
kind: Literal["remote", "skills-sh"] = "remote"
base_url: str = ""    # optional; defaults to "https://skills.sh" for kind=skills-sh
```

When `kind == 'skills-sh'`:
- Fill `base_url = "https://skills.sh"` before persistence if empty.
- Skip `_validate_registry_base_url()`.

`SkillRegistry.kind` (model column) must be passed through
`SkillRegistryRepository.create()` — currently hardcoded to `"remote"`.

Backend also needs a **DELETE** endpoint (`DELETE /admin/skill-registries/{id}`)
— currently missing, required by the admin UI.

### Error handling

| Failure | Behaviour |
|---|---|
| `skills.sh /api/search` non-200 | Return `[]`; fan-out continues |
| GitHub rate limit (403/429) | Raise `ValueError` → install 502 |
| GitHub file not found (404) | Raise `ValueError` → install 400 |
| Bundle > 50 MB | Raise `ValueError` → install 400 |
| `SKILL.md` absent in tree | Raise `ValueError` → install 400 |

Discovery errors swallowed per `SkillDiscoveryService.discover()` pattern.

### Install path

No changes. `SkillInstallService._install_remote()`:
`decode_candidate_id` → `kind="remote"`, `source_id=<row_id>` →
`manager.adapter_by_id(source_id)` → `SkillsShAdapter.fetch(source_ref)` →
`_publish_from_files()`.

---

## Part 3 — Admin Skill Registries Page

### Route

`frontend/packages/web/app/admin/skill-registries/page.tsx`

Added to admin sidebar nav as **"Skill Registries"** (中文: **技能仓库**),
between "Skills" and the next item.

### Layout

Master-detail, same pattern as `admin/skills`:

```
┌──────────────────────────────────────────────────────┐
│ Skill Registries  (header)                           │
│ Manage external skill registries for this org        │
├──────────────────────────────────────────────────────┤
│ [+ Add Registry]                    (toolbar)        │
├────────────────────┬─────────────────────────────────┤
│ List (300px)       │ Detail / Add form               │
│                    │                                 │
│ ● skills.sh        │  [selected registry detail]     │
│   community · on   │                                 │
│                    │                                 │
│ ● My Registry      │                                 │
│   untrusted · off  │                                 │
└────────────────────┴─────────────────────────────────┘
```

### Left sidebar — registry list

Each row: icon (Globe for remote/skills-sh), name, kind badge, trust badge,
enabled/disabled indicator. Clicking selects and opens detail panel.

Empty state: "No registries configured. Add one to enable skill discovery
from external sources."

### Right panel — detail view

Shows for a selected registry:
- Name, kind (`skills.sh` or `Custom`), base_url (for custom), trust tier
- Enabled toggle (calls `PATCH /admin/skill-registries/{id}`)
- Trust tier selector: Official / Community / Untrusted
- Delete button with confirmation dialog

### Right panel — add form

Opened by "+ Add Registry" button (clears selection):

```
Kind:       [skills.sh ▼]  /  [Custom Registry ▼]

  skills.sh selected:
    Name:        [_______________]   (pre-filled "skills.sh", editable)
    Trust tier:  [Community ▼]

  Custom selected:
    Name:        [_______________]
    Registry URL:[_______________]   (validated: must be public HTTPS)
    Trust tier:  [Untrusted ▼]

[Cancel]  [Add Registry]
```

On submit: `POST /admin/skill-registries` → success refreshes list and
selects the new row.

### Data fetching

Follow the same pattern as `admin/skills`: **direct fetch with
`credentials: 'include'`**, no Next.js proxy routes. The admin API routes
(`/api/v1/admin/...`) are reachable directly from the browser via the
existing Next.js rewrite that forwards all `/api/v1/*` requests to the
backend. CSRF tokens are read from the `cubebox_csrf` cookie (same as
all other admin pages).

---

## Files changed

### Backend

| File | Change |
|---|---|
| `cubebox/models/skill_source.py` → `skill_registry.py` | Rename model + class; remove hardcoded `kind="remote"` |
| `cubebox/repositories/skill_source.py` → `skill_registry.py` | Rename; pass `kind` through `create()` |
| `cubebox/skills/sources/base.py` | Rename `SkillSource` protocol → `SkillRegistryAdapter` |
| `cubebox/skills/sources/local.py` | Rename `LocalCatalogSource` → `LocalCatalogAdapter` |
| `cubebox/skills/sources/remote.py` | Rename `RemoteRegistrySource` → `RemoteRegistryAdapter` |
| `cubebox/skills/sources/registry.py` | Rename `SkillSourceRegistry` → `SkillsAdapterManager`; add `skills-sh` branch |
| `cubebox/skills/sources/skills_sh.py` | **New** — `SkillsShAdapter` |
| `cubebox/api/routes/v1/admin_skill_sources.py` → `admin_skill_registries.py` | Rename; add `kind` field; optional `base_url`; add DELETE endpoint |
| `cubebox/api/app.py` | Update router import |
| `cubebox/db/alembic/versions/<new>.py` | `ALTER TABLE skill_sources RENAME TO skill_registries` |
| `cubebox/models/__init__.py` | Update export |
| `config.yaml` | Add `registry.skills_sh.github_token` |
| `tests/unit/test_skills_sh_adapter.py` | **New** — `httpx.MockTransport` tests for search + fetch |
| `tests/e2e/test_skill_registries_admin.py` | Update existing test imports/names |

### Frontend

| File | Change |
|---|---|
| `app/admin/skill-registries/page.tsx` | **New** — Skill Registries admin page |
| `components/admin/skill-registries/RegistryList.tsx` | **New** |
| `components/admin/skill-registries/RegistryDetailPanel.tsx` | **New** |
| `components/admin/skill-registries/AddRegistryForm.tsx` | **New** |
| `hooks/useAdminSkillRegistries.ts` | **New** — SWR hook for list + mutations (direct fetch, credentials: include) |
| `components/admin/AdminSubNav.tsx` | Add `{ href: '/admin/skill-registries', label: t('skillRegistries'), icon: Database }` entry after "Skills" |
| `messages/en.json` | Add `adminSkillRegistries.*` keys |
| `messages/zh.json` | Add Chinese translations (`技能仓库`) |
