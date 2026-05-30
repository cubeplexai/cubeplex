# skills.sh Registry Source — Design

**Date:** 2026-05-30
**Status:** Draft

## Problem

The skill discovery system supports pluggable `SkillSource` backends. The existing
`RemoteRegistrySource` expects a custom REST protocol (`/search`, `/tree/{ref}`,
`/raw/{ref}/{file}`). skills.sh — the registry behind `npx skills` — uses a
different API (`/api/search`, GitHub tree + raw for file fetch). Neither a config
value nor a URL change makes `RemoteRegistrySource` speak skills.sh's protocol;
a dedicated adapter is required.

## Goal

An admin can add a skills.sh source in the admin UI (`kind = 'skills-sh'`). Once
enabled, skill discovery fans out to skills.sh in addition to the local catalog.
Search results appear in the workspace Skills page. Install pulls the SKILL.md
bundle from GitHub and imports it into the org catalog.

## skills.sh API (observed from `npx skills` v1.5.9)

### Search

```
GET https://skills.sh/api/search?q={query}&limit={n}

Response: {
  "skills": [
    {
      "name":     "frontend-design",   // display slug
      "id":       "frontend-design",   // same as name
      "source":   "vercel-labs/skills" // "{owner}/{repo}"
      "installs": 1200
    }
  ]
}
```

### File fetch

No single download endpoint with a known stable response shape. Files are pulled
directly from GitHub:

- Tree list: `GET https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1`
- File content: `GET https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}`

The skill lives at `{skill_slug}/` inside the repo (e.g.,
`vercel-labs/skills` tree → `frontend-design/SKILL.md`,
`frontend-design/references/guide.md`, …).

## Design

### 1. `source_ref` encoding

`source_ref` carried in the opaque `candidate_id` encodes everything needed
to fetch later, without a server lookup:

```
{owner}/{repo}/{branch}/{skill_slug}
e.g.  vercel-labs/skills/main/frontend-design
```

Branch is resolved at **search time**, not fetch time: `SkillsShSource.search()`
makes one `GET /repos/{owner}/{repo}` call per distinct `{owner}/{repo}` in the
result set (typically one — the vercel-labs/skills mono-repo) to read
`default_branch`, then encodes it into every candidate's `source_ref`. This
ensures installs are stable: the same `candidate_id` always fetches from the
same branch ref, even if the repo later changes its default branch.

Splitting on `"/"` with `split("/", 3)` unambiguously yields
`(owner, repo, branch, slug)` since owner, repo, and branch names cannot contain
`/`; only slug follows and it may itself contain hyphens but not slashes.

### 2. New class: `SkillsShSource`

File: `backend/cubebox/skills/sources/skills_sh.py`

```
class SkillsShSource:
    kind: SourceKind = "remote"   # keeps install routing compatible

    __init__(source_id, trust_tier, source_name, github_token | None)

    async search(query, *, limit) -> list[SkillCandidate]
        GET https://skills.sh/api/search?q=...&limit=...
        Collect distinct {owner}/{repo} values from results
        For each unique repo: GET https://api.github.com/repos/{owner}/{repo}
          → default_branch (cached within this call, not across calls)
        For each result:
          source_ref = f"{skill['source']}/{branch}/{skill['id']}"
          candidate_id = encode_candidate_id("remote", source_ref, source_id=self.source_id)
          repo field = f"https://github.com/{skill['source']}"

    async fetch(source_ref) -> dict[str, bytes]
        Parse source_ref → owner, repo, branch, slug  (split("/", 3))
        GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1
        Filter entries whose path starts with "{slug}/"
        Strip the "{slug}/" prefix → relative path key
        Download each file from raw.githubusercontent.com
        Return {rel_path: bytes}
        Raise ValueError on HTTP errors, missing SKILL.md, or size violations
```

`SkillsShSource` is **not** a subclass of `RemoteRegistrySource`; it implements
the `SkillSource` protocol directly.

Size caps (`_RAW_FILE_MAX_BYTES = 10 MB`, `_BUNDLE_MAX_BYTES = 50 MB`) are
redeclared locally in `skills_sh.py` — importing private names from `remote.py`
would create a hidden coupling to another module's internals.

### 3. `skill_source` table — new kind value

The `kind` column (currently always `'remote'`) gains a new allowed value:
`'skills-sh'`. No migration needed — `kind` is a plain `VARCHAR(16)` with no
DB-level check constraint.

### 4. Admin API changes

**`CreateSkillSourceRequest`** gains two changes:

```python
kind: Literal["remote", "skills-sh"] = "remote"
base_url: str = ""          # was required; now optional (empty = use kind default)
```

When `kind == 'skills-sh'`:
- `base_url` is set to `"https://skills.sh"` before persistence if empty.
- `_validate_registry_base_url()` is skipped (endpoint is hardcoded, not admin-supplied).
- Row is stored with `kind='skills-sh'` and `base_url='https://skills.sh'`.

`SkillSource.kind` (the model column) must also be passed through
`SkillSourceRepository.create()` — the method currently hardcodes `kind="remote"`.
Both the model default and the repository create signature need to accept the value.

`SkillSourceResponse` already surfaces `kind`; no change needed there.

### 5. `SkillSourceRegistry.build()` routing

```python
for row in rows:
    if row.kind == "skills-sh":
        sources.append(SkillsShSource(
            source_id=row.id,
            trust_tier=TrustTier(row.trust_tier),
            source_name=row.name,
            github_token=settings.registry.skills_sh.github_token or None,
        ))
    else:  # "remote"
        sources.append(RemoteRegistrySource(...))
```

`remote_source_by_id()` already matches on `s.kind == "remote"` and
`s.source_id`. Since `SkillsShSource.kind = "remote"`, no changes needed there.

### 6. Config

`config.yaml` (under the top-level `default:` block):

```yaml
registry:
  skills_sh:
    github_token: ""   # optional; raises GitHub rate limit 60 → 5000 req/h
```

The key uses an **underscore** (`skills_sh`), not a hyphen. Dynaconf's dotted
`settings.get("a.b")` treats hyphens as ambiguous; underscore keys are
accessible as `settings.registry.skills_sh.github_token` without special casing.

Operators who want higher rate limits add the token to
`config.development.local.yaml` or set `CUBEBOX_REGISTRY__SKILLS_SH__GITHUB_TOKEN`
as an environment variable (dynaconf convention).

### 7. Install path

No changes. `SkillInstallService._install_remote()` already handles the case:
`decode_candidate_id` → `kind="remote"`, `source_id=<row_id>` →
`registry.remote_source_by_id(source_id)` returns the `SkillsShSource` →
`source.fetch(source_ref)` downloads from GitHub → `_publish_from_files()`.

## Error handling

| Failure | Behaviour |
|---|---|
| `skills.sh /api/search` returns non-200 | Log, return `[]` (discovery continues from other sources) |
| GitHub API rate limit (403/429) | Raise `ValueError("rate limited")` → install returns 502 |
| GitHub file not found (404) | Raise `ValueError` → install returns 400 |
| Bundle > 50 MB | Raise `ValueError` → install returns 400 |
| `SKILL.md` missing in tree | Raise `ValueError` → install returns 400 |

Discovery errors are swallowed per the existing fan-out pattern in
`SkillDiscoveryService.discover()` (`except Exception: continue`).

## Out of scope

- Frontend admin UI for managing skill sources (pre-existing gap, separate task).
- Pagination of skills.sh search results beyond `limit`.
- Caching search results or GitHub tree responses.
- Per-org GitHub tokens (single token from config is sufficient).

## Files changed

| File | Change |
|---|---|
| `backend/cubebox/skills/sources/skills_sh.py` | New |
| `backend/cubebox/skills/sources/registry.py` | Add `skills-sh` branch in `build()` |
| `backend/cubebox/models/skill_source.py` | Remove hardcoded `kind="remote"` default, accept any kind |
| `backend/cubebox/repositories/skill_source.py` | Pass `kind` through `create()` |
| `backend/cubebox/api/routes/v1/admin_skill_sources.py` | Add `kind` field, make `base_url` optional, skip URL validation for `skills-sh` |
| `backend/config.yaml` | Add `registry.skills_sh.github_token` |
| `backend/tests/unit/test_skills_sh_source.py` | New unit tests with `httpx.MockTransport` |
