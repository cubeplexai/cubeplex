# Skill Search UX — Design Spec

**Date:** 2026-06-01  
**Branch:** feat/skill-search-ux

---

## Problem

When a user asks the agent to find a skill (e.g., "find a Twitter management skill"), the
current flow has three failure modes:

1. **Empty descriptions.** Most remote skills from skills.sh return `description: ""`. The agent
   cannot evaluate relevance, so it guesses.
2. **Wrong tool for uninstalled skills.** The agent tries `load_skill(canonical_name)` on skills
   that aren't enabled, getting three consecutive errors. `load_skill` only works for already-
   installed skills; the hint in `find_skills` says to use `candidate_id` for install, but the
   agent ignores it.
3. **No preview path.** There is no mechanism — for the agent or the user — to read a skill's
   `SKILL.md` before installing it. Discovery stops at a name and an empty description.

---

## Goals

- Agent can read a skill's content before suggesting installation (`preview_skill` tool).
- Agent can install a skill when the user explicitly asks (`install_skill` tool).
- `find_skills` results render as visual skill cards in the chat UI instead of raw JSON.
- Each card has a Preview button (opens right panel) and an Install button (inline install).
- Cards show install/download count.

---

## Non-goals

- Enriching descriptions at search time (too slow; preview-on-demand is sufficient).
- Changing the `find_skills` ranking or scoring logic.
- Auto-installing without user request.

---

## Architecture

Three independent deliverables that can be built and shipped separately:

| # | What | Where |
|---|------|--------|
| 1 | `preview_skill` agent tool | Backend |
| 2 | `install_skill` agent tool | Backend |
| 3 | `find_skills` card rendering + right-panel preview | Frontend |

No new HTTP endpoints are needed. The frontend Preview button calls the existing
`GET /ws/{ws}/skills/discover/preview?candidate_id=xxx`, which already handles both local
and remote candidates.

---

## Backend

### `preview_skill` tool

**File:** `backend/cubebox/tools/builtin/preview_skill.py`

```
Input:  candidate_id: str
Output: { candidate_id, name, content: str (SKILL.md), env_vars: list[str] }
        or error string on failure
```

Internally calls `SkillsAdapterManager` + `source.fetch(source_ref)` for remote candidates,
or `SkillCatalogService.fetch_skill_md` for local ones — the same logic already in
`GET /discover/preview`. Returns SKILL.md content as plain text so the agent can read it
and describe the skill to the user before recommending installation.

Registration: same pattern as `find_skills` in `run_manager.py`.

### `install_skill` tool

**File:** `backend/cubebox/tools/builtin/install_skill.py`

```
Input:  candidate_id: str
Output: { installed: true, canonical_name: str, version: str }
        or error string on failure
```

Calls `SkillInstallService.install(candidate_id)`. The agent should only call this when the
user has explicitly requested installation in the conversation (not proactively). On success,
the agent can immediately call `load_skill(canonical_name)` to use the skill.

Registration: same pattern as `find_skills`.

---

## Frontend

### Auto-rendering `find_skills` results

The message renderer detects a `tool_result` where `tool_name == "find_skills"` and renders
`<SkillSearchResults>` instead of the default JSON text block. The candidates array is parsed
directly from the tool result JSON.

**Files to create/modify:**
- `components/chat/tool-results/SkillSearchResults.tsx` — container + card list
- `components/chat/tool-results/SkillCandidateCard.tsx` — individual card
- Modify the tool-result renderer to route `find_skills` → `SkillSearchResults`

### `SkillCandidateCard`

Displays per candidate:

| Field | Display |
|-------|---------|
| `name` | Bold title |
| `description` | Body text; "No description available" when empty |
| `trust` | Badge: official (blue) / community (amber) / unvetted (grey) |
| `install_state` | Badge: enabled (green) / available |
| `install_count` | Download count with icon (hidden when null) |
| `source_name` | Muted label (skills.sh / Clawhub / catalog) |

Two action buttons:
- **Preview** — calls `openSkillCandidate(candidateId)` → opens right panel
- **Install** — calls `POST /ws/{ws}/skills/install`, then updates card to "enabled" on success;
  button disabled while in-flight and after install

### Right-panel `skill-candidate` view

**New panel view type** in `panelStore`:
```ts
| { type: 'skill-candidate'; candidateId: string }
```

New action: `openSkillCandidate(candidateId: string)`.

**New component:** `components/panel/SkillCandidatePanel.tsx`
- Fetches `GET /ws/{ws}/skills/discover/preview?candidate_id=xxx` via SWR
- Shows loading state while fetching (GitHub fetch can take 1-3 s)
- Renders SKILL.md as markdown using existing `ReactMarkdown + remarkGfm` pattern
- Shows name, trust badge, install count at top
- Install button at bottom (same behavior as card button)

AppShell adds a branch for `view.type === 'skill-candidate'` rendering `<SkillCandidatePanel>`.

---

## Data Flow

```
User: "find a Twitter skill"
  → agent: find_skills(query="Twitter automation")
    → returns 10 candidates (JSON)
    → frontend auto-renders as SkillSearchResults cards

User clicks Preview on card
  → frontend: openSkillCandidate(candidateId)
  → right panel opens SkillCandidatePanel
  → fetches GET /discover/preview?candidate_id=xxx
  → renders SKILL.md markdown

User clicks Install on card (or says "install the second one")
  → frontend: POST /skills/install  (UI path)
  OR
  → agent: install_skill(candidateId)  (conversation path)
  → on success: card badge → "enabled"; agent can load_skill immediately
```

---

## Error Handling

- `preview_skill` tool: remote fetch failures (GitHub down, 404) return an error string; agent
  surfaces it as "couldn't fetch preview, here's what I know from the description".
- `install_skill` tool: returns error string on `SkillInstallError`; agent tells user what went
  wrong (trust tier, not found, etc.).
- `SkillCandidatePanel`: shows an error state if the fetch fails; user can retry.
- Card Install button: shows toast on failure, button re-enabled for retry.

---

## Testing

- **Unit:** `preview_skill` tool with mocked `SkillsAdapterManager` (local + remote paths).
- **Unit:** `install_skill` tool with mocked `SkillInstallService`.
- **E2E:** `find_skills` → card renders with correct fields (description, trust badge, install
  count) → Preview opens panel with markdown → Install updates card state.
