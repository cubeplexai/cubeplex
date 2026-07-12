# Skill Env Vars Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse `requires.env` from Clawhub SKILL.md frontmatter and display required env var names in the skill detail panels (both workspace and admin views).

**Architecture:** Fix the frontmatter parser to also expand `metadata.openclaw` nesting (Clawhub's convention). Add `extract_env_vars(raw_metadata)` as a public pure function in `frontmatter.py` — both route files import it (reuse goes one layer down, not at route layer). Expose `env_vars: list[str]` in `CandidatePreviewResponse`. Frontend exports `SkillPreviewResponse` from `@cubeplex/core` and shares it across both detail panels.

**Tech Stack:** Python (FastAPI + Pydantic), TypeScript (Next.js + React 19), SWR for data fetching.

---

## File Map

| File | Change |
|---|---|
| `backend/cubeplex/skills/frontmatter.py` | Extend alias expansion; add public `extract_env_vars()` |
| `backend/tests/unit/test_skill_frontmatter.py` | Tests for `metadata.openclaw` nesting + `extract_env_vars` |
| `backend/cubeplex/api/schemas/skill_discovery.py` | Add `env_vars: list[str]` to `CandidatePreviewResponse` |
| `backend/cubeplex/api/routes/v1/ws_skills.py` | Import `extract_env_vars`; populate field in both preview paths |
| `backend/cubeplex/api/routes/v1/admin_skills.py` | Same |
| `frontend/packages/core/src/api/skills.ts` | Add + export `SkillPreviewResponse` interface |
| `frontend/packages/core/src/index.ts` | Re-export `SkillPreviewResponse` if not auto-exported |
| `frontend/packages/web/components/skills/CandidateDetailPanel.tsx` | Import shared type; render env_vars row |
| `frontend/packages/web/components/admin/skills/AdminCandidateDetailPanel.tsx` | Same |

---

## Task 1: Fix frontmatter parser — handle `metadata.openclaw` nesting + extract_env_vars

**Files:**
- Modify: `backend/cubeplex/skills/frontmatter.py`
- Test: `backend/tests/unit/test_skill_frontmatter.py`

### Background

Clawhub skills use:
```yaml
metadata:
  openclaw:
    requires:
      env: [MY_API_KEY]
```

The current parser only expands top-level `openclaw`/`clawdbot`/`clawdis`. Fix: also expand `metadata.{alias}`, with precedence: top-level alias > `metadata.alias` > bare top-level keys. Also add `extract_env_vars(raw_metadata)` as a public helper so route files don't duplicate the extraction logic.

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/unit/test_skill_frontmatter.py`:

```python
from cubeplex.skills.frontmatter import (
    InvalidFrontmatterError,
    extract_env_vars,
    parse_skill_md,
)


def test_metadata_openclaw_promoted_to_raw_metadata() -> None:
    """metadata.openclaw keys are promoted, same as top-level openclaw."""
    text = """---
name: x
description: y
version: 0.1
metadata:
  openclaw:
    requires:
      bins: [node]
      env: [MY_API_KEY]
    primaryEnv: MY_API_KEY
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["requires"] == {"bins": ["node"], "env": ["MY_API_KEY"]}
    assert fm.raw_metadata["primaryEnv"] == "MY_API_KEY"


def test_metadata_alias_does_not_override_top_level_alias() -> None:
    """Top-level openclaw wins over metadata.openclaw."""
    text = """---
name: x
description: y
version: 0.1
metadata:
  openclaw:
    requires:
      env: [FROM_METADATA]
openclaw:
  requires:
    env: [FROM_TOPLEVEL]
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["requires"] == {"env": ["FROM_TOPLEVEL"]}


def test_metadata_alias_overrides_bare_top_level() -> None:
    """metadata.openclaw wins over bare (non-alias) top-level keys."""
    text = """---
name: x
description: y
version: 0.1
requires:
  env: [BARE]
metadata:
  openclaw:
    requires:
      env: [FROM_METADATA]
---
"""
    fm = parse_skill_md(text)
    assert fm.raw_metadata["requires"] == {"env": ["FROM_METADATA"]}


def test_extract_env_vars_from_metadata_openclaw() -> None:
    text = """---
name: cuecue-deep-research
description: desc
version: 1.0.0
metadata:
  openclaw:
    requires:
      env: [CUECUE_API_KEY]
---
"""
    fm = parse_skill_md(text)
    assert extract_env_vars(fm.raw_metadata) == ["CUECUE_API_KEY"]


def test_extract_env_vars_from_toplevel_openclaw() -> None:
    text = """---
name: x
description: y
version: 1.0.0
openclaw:
  requires:
    env: [MY_KEY, OTHER_KEY]
---
"""
    fm = parse_skill_md(text)
    assert extract_env_vars(fm.raw_metadata) == ["MY_KEY", "OTHER_KEY"]


def test_extract_env_vars_empty_when_no_requires() -> None:
    fm = parse_skill_md("---\nname: x\ndescription: y\nversion: 1.0.0\n---\n")
    assert extract_env_vars(fm.raw_metadata) == []


def test_extract_env_vars_empty_when_requires_has_no_env() -> None:
    text = """---
name: x
description: y
version: 1.0.0
openclaw:
  requires:
    bins: [node]
---
"""
    fm = parse_skill_md(text)
    assert extract_env_vars(fm.raw_metadata) == []
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_skill_frontmatter.py -k "metadata or extract_env" -v
```

Expected: failures on the 7 new tests.

- [ ] **Step 3: Fix the parser + add extract_env_vars**

In `backend/cubeplex/skills/frontmatter.py`:

**a) Update the alias expansion block** (replace lines 97–102):

```python
    raw_metadata: dict[str, Any] = dict(data)

    # Expand metadata.{alias} first (lower priority than top-level aliases).
    # Clawhub publishes skills with metadata.openclaw nesting; this normalises it.
    metadata_block = raw_metadata.get("metadata")
    if isinstance(metadata_block, dict):
        for alias in _OPENCLAW_ALIASES:
            nested = metadata_block.get(alias)
            if isinstance(nested, dict):
                for k, v in nested.items():
                    raw_metadata[k] = v  # overrides bare top-level keys

    # Top-level aliases override everything (including metadata.alias results above).
    for alias in _OPENCLAW_ALIASES:
        nested = raw_metadata.pop(alias, None)
        if isinstance(nested, dict):
            for k, v in nested.items():
                raw_metadata[k] = v
```

**b) Add public helper after the `_normalise_keywords` function** (at end of file):

```python
def extract_env_vars(raw_metadata: dict[str, Any]) -> list[str]:
    """Return the list of required env var names from a parsed skill's raw_metadata.

    Reads raw_metadata["requires"]["env"] after alias expansion by parse_skill_md.
    Returns [] when absent or malformed.
    """
    requires = raw_metadata.get("requires")
    if not isinstance(requires, dict):
        return []
    env_list = requires.get("env")
    if not isinstance(env_list, list):
        return []
    return [str(e) for e in env_list if isinstance(e, str) and e]
```

- [ ] **Step 4: Run full frontmatter test suite**

```bash
cd backend && uv run pytest tests/unit/test_skill_frontmatter.py -v
```

Expected: all pass.

- [ ] **Step 5: Run mypy**

```bash
cd backend && uv run mypy cubeplex/skills/frontmatter.py
```

Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/skills/frontmatter.py backend/tests/unit/test_skill_frontmatter.py
git commit -m "fix(skills): expand metadata.openclaw alias; add extract_env_vars helper"
```

---

## Task 2: Add env_vars to preview API response

**Files:**
- Modify: `backend/cubeplex/api/schemas/skill_discovery.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_skills.py`
- Modify: `backend/cubeplex/api/routes/v1/admin_skills.py`

### Background

Both preview endpoints fetch and return SKILL.md content. We now also call `parse_skill_md` + `extract_env_vars` on that content and include the result in the response. For local skills (kind=local), the content is fetched via `catalog.fetch_skill_md`. `env_vars` defaults to `[]`.

- [ ] **Step 1: Add `env_vars` to schema**

In `backend/cubeplex/api/schemas/skill_discovery.py`:

```python
class CandidatePreviewResponse(BaseModel):
    candidate_id: str
    name: str
    canonical_name: str
    content: str
    env_vars: list[str] = []
```

- [ ] **Step 2: Update ws_skills route**

In `backend/cubeplex/api/routes/v1/ws_skills.py`, add import (check existing imports first — if `peek_skill_name` is already imported from frontmatter, add `extract_env_vars` to that same import line):

```python
from cubeplex.skills.frontmatter import extract_env_vars, parse_skill_md, peek_skill_name
```

Add a module-level helper that wraps the extraction with error handling (SKILL.md from remote might be malformed):

```python
def _env_vars_from_skill_md(content: str) -> list[str]:
    try:
        fm = parse_skill_md(content, default_version="0.0.0")
    except Exception:
        return []
    return extract_env_vars(fm.raw_metadata)
```

Update local skill preview return (around line 234):

```python
        content = await catalog.fetch_skill_md(sv.id)
        return CandidatePreviewResponse(
            candidate_id=candidate_id,
            name=skill.name,
            canonical_name=skill.name,
            content=content,
            env_vars=_env_vars_from_skill_md(content),
        )
```

Update remote skill preview return (around line 260):

```python
    return CandidatePreviewResponse(
        candidate_id=candidate_id,
        name=slug,
        canonical_name=f"{org.slug}:{slug}",
        content=content,
        env_vars=_env_vars_from_skill_md(content),
    )
```

- [ ] **Step 3: Update admin_skills route**

In `backend/cubeplex/api/routes/v1/admin_skills.py`, add import:

```python
from cubeplex.skills.frontmatter import extract_env_vars, parse_skill_md, peek_skill_name
```

Add the same thin wrapper (each route file owns its own private wrappers, the shared logic is in frontmatter.py):

```python
def _env_vars_from_skill_md(content: str) -> list[str]:
    try:
        fm = parse_skill_md(content, default_version="0.0.0")
    except Exception:
        return []
    return extract_env_vars(fm.raw_metadata)
```

Update `admin_preview_candidate` return:

```python
    return CandidatePreviewResponse(
        candidate_id=candidate_id,
        name=name,
        canonical_name=name,
        content=skill_md,
        env_vars=_env_vars_from_skill_md(skill_md),
    )
```

- [ ] **Step 4: Run mypy**

```bash
cd backend && uv run mypy cubeplex/skills/frontmatter.py cubeplex/api/schemas/skill_discovery.py cubeplex/api/routes/v1/ws_skills.py cubeplex/api/routes/v1/admin_skills.py
```

Expected: `Success: no issues found`.

- [ ] **Step 5: Run affected unit tests**

```bash
cd backend && uv run pytest tests/unit/test_skill_frontmatter.py tests/unit/ -k "skill" -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add \
  backend/cubeplex/api/schemas/skill_discovery.py \
  backend/cubeplex/api/routes/v1/ws_skills.py \
  backend/cubeplex/api/routes/v1/admin_skills.py
git commit -m "feat(skills): expose env_vars in skill preview API response"
```

---

## Task 3: Frontend — shared type + display env vars

**Files:**
- Modify: `frontend/packages/core/src/api/skills.ts`
- Modify: `frontend/packages/core/src/api/adminSkills.ts`
- Modify: `frontend/packages/web/components/skills/CandidateDetailPanel.tsx`
- Modify: `frontend/packages/web/components/admin/skills/AdminCandidateDetailPanel.tsx`

### Background

Define `SkillPreviewResponse` once in `@cubeplex/core/src/api/skills.ts`, export it, and use it in `adminPreviewCandidate` and both detail panels' SWR fetchers. Both panels render a "Requires env" row when `env_vars` is non-empty.

- [ ] **Step 1: Add shared type to core**

In `frontend/packages/core/src/api/skills.ts`, add after the existing interfaces:

```typescript
export interface SkillPreviewResponse {
  content: string
  env_vars: string[]
}
```

Check `frontend/packages/core/src/index.ts` (or equivalent barrel) to confirm `skills.ts` exports are re-exported. If the barrel has `export * from './api/skills'`, no further change is needed.

- [ ] **Step 2: Update `adminPreviewCandidate`**

In `frontend/packages/core/src/api/adminSkills.ts`:

```typescript
import { toApiError } from './client'
import type { SkillCandidateOut, SkillPreviewResponse } from './skills'

export async function adminPreviewCandidate(candidateId: string): Promise<SkillPreviewResponse> {
  const params = new URLSearchParams({ candidate_id: candidateId })
  const res = await fetch(`/api/v1/admin/skills/discover/preview?${params}`, {
    credentials: 'include',
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SkillPreviewResponse
}
```

(Remove the old `{ content: string }` inline return type.)

- [ ] **Step 3: Update `CandidateDetailPanel.tsx`**

In `frontend/packages/web/components/skills/CandidateDetailPanel.tsx`:

**a) Import shared type** (add to existing `@cubeplex/core` import):

```typescript
import { createApiClient, useSkillsStore, type SkillCandidateOut, type SkillPreviewResponse } from '@cubeplex/core'
```

**b) Replace the local `previewFetcher` type** (lines 44–48) — remove `interface SkillPreview` if you added one; use `SkillPreviewResponse` directly:

```typescript
async function previewFetcher(url: string): Promise<SkillPreviewResponse> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.json() as Promise<SkillPreviewResponse>
}
```

**c) Update SWR type** (line 71):

```typescript
  const { data: preview, isLoading } = useSWR<SkillPreviewResponse>(
    `/api/v1/ws/${wsId}/skills/discover/preview?candidate_id=${candidate.candidate_id}`,
    previewFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
```

**d) Add env_vars row** — insert after the closing `)}` of the "Repo" `<div>` and before `</dl>` (around line 131):

```tsx
        {preview?.env_vars && preview.env_vars.length > 0 && (
          <div className="flex items-start gap-3">
            <dt className="min-w-20 pt-0.5 text-xs font-medium text-muted-foreground">
              Requires env
            </dt>
            <dd className="flex flex-wrap gap-1">
              {preview.env_vars.map((v) => (
                <code
                  key={v}
                  className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-foreground"
                >
                  {v}
                </code>
              ))}
            </dd>
          </div>
        )}
```

- [ ] **Step 4: Update `AdminCandidateDetailPanel.tsx`**

In `frontend/packages/web/components/admin/skills/AdminCandidateDetailPanel.tsx`:

**a) Import shared type**:

```typescript
import { useAdminSkillsStore, type SkillCandidateOut, type SkillPreviewResponse } from '@cubeplex/core'
```

**b) Replace `previewFetcher`** (lines 40–44):

```typescript
async function previewFetcher(url: string): Promise<SkillPreviewResponse> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.json() as Promise<SkillPreviewResponse>
}
```

**c) Update SWR type** (line 65):

```typescript
  const { data: preview, isLoading } = useSWR<SkillPreviewResponse>(
    `/api/v1/admin/skills/discover/preview?candidate_id=${candidate.candidate_id}`,
    previewFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
```

**d) Add env_vars row** — after the "Repo" row, before `</dl>` (around line 134):

```tsx
        {preview?.env_vars && preview.env_vars.length > 0 && (
          <div className="flex items-start gap-3">
            <dt className="min-w-24 pt-0.5 text-xs font-medium text-muted-foreground">
              Requires env
            </dt>
            <dd className="flex flex-wrap gap-1">
              {preview.env_vars.map((v) => (
                <code
                  key={v}
                  className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-foreground"
                >
                  {v}
                </code>
              ))}
            </dd>
          </div>
        )}
```

- [ ] **Step 5: Build core package (required before web sees type changes)**

```bash
cd frontend && pnpm --filter @cubeplex/core build
```

Expected: exits 0.

- [ ] **Step 6: TypeScript check**

```bash
cd frontend && pnpm --filter @cubeplex/core tsc --noEmit && pnpm --filter web tsc --noEmit
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add \
  frontend/packages/core/src/api/skills.ts \
  frontend/packages/core/src/api/adminSkills.ts \
  frontend/packages/web/components/skills/CandidateDetailPanel.tsx \
  frontend/packages/web/components/admin/skills/AdminCandidateDetailPanel.tsx
git commit -m "feat(skills): display required env vars in skill detail panels"
```
