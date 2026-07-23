# Agent tools to read/update workspace persona

Related: #397

## Goal

Let the agent **read and update** the workspace persona
(`AgentConfig.system_prompt`) from a normal chat turn, so users can refine
standing instructions in conversation and have Settings → Persona stay the
same source of truth.

## Context

### What persona is (and is not)

| User language | Product object | Confusion to avoid |
| --- | --- | --- |
| Persona / workspace system instructions | `AgentConfig.system_prompt` (1:1 with workspace) | Default workspace **named** “Personal” |
| Personal **memory** | Memory items via `memory_save` / `memory_update` | Atomic facts/preferences, not the persona document |
| Account “Personal info” | User profile | Out of scope |

### Today

| Piece | Location | Agent access |
| --- | --- | --- |
| Model | `models/agent_config.py` — `system_prompt` Text | No tool |
| API | `GET/PUT /api/v1/ws/{ws}/settings/agent` | Settings UI; `require_member`; max **8000** chars |
| UI | `PersonaEditor.tsx` + workspace settings store | Manual edit |
| Injection | `run_manager` each run: `BASE_SYSTEM_PROMPT + "\n\n" + agent_cfg.system_prompt` | Re-read at assembly |
| Memory tools | `tools/builtin/memory.py` | Different layer |

There is no `persona_*` tool. Users who discover style prefs mid-chat must either
use memory tools (wrong surface for a full role document) or open Settings.

### Prompt cache

Persona sits in the **stable system prefix**. Changing it **invalidates** the
prompt cache for subsequent turns. That is expected and acceptable — updates
are rare. Align with `backend/docs/prompt-cache-discipline.md`: do not put
persona in the unstable tail.

## Goals

1. Agent can **read** current workspace persona text during a conversation.
2. Agent can **apply** updates to the same DB row Settings uses.
3. Changes are durable and appear on the **next** model assembly (not mid-stream
   hot-patch of the current turn).
4. **HITL confirmation** before overwriting a non-empty persona (high impact in
   shared workspaces).
5. Prompt copy teaches **persona vs memory**.

## Non-goals

- Per-conversation instruction overrides that never touch workspace settings.
- Editing `BASE_SYSTEM_PROMPT` or middleware fragments (sandbox, citations, …).
- Replacing the memory system.
- Full persona version history / VCS (optional later).

## Design

### When to use persona vs memory

| Prefer **persona** | Prefer **memory** |
| --- | --- |
| User asks to change “persona / system instructions / 人设 / always behave as…” | Small typed fact: preference, correction, project_fact, … |
| Standing role or policy block for the whole workspace agent | Atomic item that should not rewrite the whole document |

### Tools

| Tool | Behavior |
| --- | --- |
| `persona_get` | Return current `system_prompt`, length, and max (8000). |
| `persona_update` | Full replace of `system_prompt`. Validate max 8000. |

**Authorization (v1):** match settings write — any workspace **member**, same as
`PUT /settings/agent`. Document shared-workspace impact in tool description.

**Write mode (v1):** full document replace only. Append / search-replace can
come in a later phase once overwrite UX is solid.

### HITL for overwrite

Recommended v1:

- **Empty → first write:** apply without confirm (still report success clearly).
- **Non-empty → replace:** require confirmation via existing HITL channel
  (`ask_user` / confirm card) **before** commit, showing a short summary of the
  change (length before/after, first ~N chars of new text, note that **all
  members** of the workspace are affected).
- Agent should call `persona_get` before proposing a replace when the user
  intent is incremental (“add X”) so it can compose a full new document.

Implementation options (choose during implementation with cubepi HITL
patterns already used for sandbox confirm):

1. Tool internally pauses for confirm when previous text non-empty, **or**
2. Prompt requires agent to `ask_user` first; server still accepts direct
   writes (weaker).

**Recommendation:** enforce on the tool path (option 1) so confirmation is not
skippable by a misbehaving model.

### When the new persona takes effect

| Moment | Behavior |
| --- | --- |
| Current streaming turn | Already assembled — no mid-turn hot-patch |
| Next user turn / next run assembly | `run_manager` re-reads `AgentConfig` (already does today) |
| Prompt cache | Stable prefix changes → cache miss; document as OK |

Verify during implementation that no process-level cache of persona bypasses
the DB on the next turn.

### Service reuse

Do not duplicate validation. Extract or call the same path as
`update_agent_config` in `ws_settings.py` (service function that sets
`system_prompt` with max length 8000).

### Frontend

1. Tool-result card: “Updated workspace persona” + short summary (not a wall of
   text unless expanded).
2. `PersonaEditor`: refetch on focus or invalidate settings store so the page
   does not stay stale after agent write.
3. User guide line: you can ask the agent to update persona.

### Security / abuse

- Enforce 8000 char cap.
- Persona is **appended after** base system prompt; base safety authority
  language stays.
- Tool cannot change org admin settings, models, or RBAC.
- Optional later: rate-limit rapid rewrites; audit `source=agent|ui`.

## Phasing

| Phase | Deliverable |
| --- | --- |
| **1** | `persona_get` + `persona_update` (full replace) + prompt guidance + e2e |
| **2** | HITL confirm for overwrite; tool result card; settings stale refresh |
| **3** | Patch/append helpers; audit metadata; optional admin-only write policy |

Ship Phase 1+2 together if HITL wiring is small enough to avoid a half-safe
overwrite path in production.

## Acceptance criteria

1. Agent can retrieve current persona via a tool in normal workspace chat.
2. After a successful update, Settings → Persona shows the new text.
3. A **new** turn in that workspace uses the updated persona in the effective
   system prompt.
4. Memory tools remain; prompt distinguishes persona vs memory.
5. Empty → first persona and non-empty → replace both work; length over 8000
   fails cleanly.
6. Overwrite of non-empty persona requires user confirmation.
7. Shared-workspace risk is disclosed in tool description and/or confirm copy.
8. Tests cover write path + authz boundary + confirmation gate.

## Open questions (v1 decisions)

| Question | Decision |
| --- | --- |
| Full replace vs patch | **Full replace** in v1 |
| Always HITL vs only overwrite | **HITL only when previous persona non-empty** |
| Admin-only agent writes? | **No** — same as UI (member) |
| Group chats | **Allowed**; confirm copy states workspace-wide effect |
| Rename UI “Persona” → “Instructions” | Separate copy issue |

## Related code

- `backend/cubeplex/models/agent_config.py`
- `backend/cubeplex/api/routes/v1/ws_settings.py`
- `backend/cubeplex/api/schemas/ws_settings.py` (`AgentConfigPatch` max 8000)
- `backend/cubeplex/tools/builtin/memory.py` (factory pattern to mirror)
- `backend/cubeplex/streams/run_manager.py` (tool list + prompt assembly)
- `backend/cubeplex/prompts/memory.py` (authoring guidance pattern)
- `frontend/.../workspace-settings/PersonaEditor.tsx`
- `backend/docs/prompt-cache-discipline.md`
- System prompt optimization: #391
