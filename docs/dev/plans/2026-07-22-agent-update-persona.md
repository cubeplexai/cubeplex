# Agent update workspace persona — implementation plan

Related: #397 · Spec: `docs/dev/specs/2026-07-22-agent-update-persona-design.md`

**Goal**: Built-in agent tools to get/update `AgentConfig.system_prompt` with
HITL on overwrite, prompt guidance vs memory, and settings UI freshness.

**Architecture**: Factory tools (like memory) call a small shared service used
by `PUT /settings/agent`. Register tools on the main chat agent tool list in
`run_manager`. Overwrite path uses existing HITL channel.

**Tech stack**: cubepi `AgentTool`, existing HITL (`ask_user_tool` /
checkpoint channel), SQLModel `AgentConfig`, React tool-result rendering.

---

## Unit 1: Shared agent-config service

**Files**:
- `backend/cubeplex/services/agent_config.py` (new) — `get_or_create`,
  `get_system_prompt`, `set_system_prompt(session, org_id, workspace_id, text)`
- Refactor `ws_settings.py` to call the service (no behavior change)

**Rules**:
- Max length 8000 (raise clear validation error).
- Preserve get-or-create race handling already in the route.

**Tests**: unit with session mock or e2e against existing settings routes
still green after refactor.

---

## Unit 2: `persona_get` + `persona_update` tools

**Files**:
- `backend/cubeplex/tools/builtin/persona.py` (new)
- Wire in `run_manager` next to `create_memory_tools` (per-request DI:
  org_id, workspace_id, session/service factory)

**Schemas** (sketch):

```python
class PersonaGetArgs(BaseModel):
    pass  # no args

class PersonaUpdateArgs(BaseModel):
    system_prompt: str = Field(max_length=8000)
    reason: str = Field(default="", max_length=500)
```

**Results**:
- get: JSON `{ "system_prompt", "length", "max_length": 8000 }`
- update success: `{ "updated": true, "length", "previous_length", ... }`
- update error: clear message (too long, not confirmed, etc.)

**Phase note (hard gate):** Units 2 and 3 **must ship in the same PR**. Do not
merge a non-empty overwrite path without tool-enforced HITL. If HITL is blocked,
reject non-empty updates or allow empty→first only.

**Registration:** attach write tool only for interactive member runs (see spec).
Automated/IM tool lists omit `persona_update`.

**Tests**:
- e2e: tool write → `GET /settings/agent` returns new text
- unit: max length rejection
- unit/e2e: non-interactive trigger does not expose write tool

---

## Unit 3: HITL on non-empty overwrite

**Files**:
- `persona.py` update path
- Reuse run-level `CheckpointedChannel` from `run_manager` (same channel as
  `ask_user_tool` / sandbox confirm)

**Cubepi constraints (must design against):**
- Calling HITL from inside a custom tool requires
  `allow_inside_custom_tool=True` on that channel **or** an equivalent
  builtin/middleware path; default channel construction rejects it
  (`HitlDurabilityNotGuaranteed`).
- Prefer `channel.ask([...])` → existing `ask_user_request` frontend path
  (supported by `hitl_resume`). Do not invent a new kind without wiring.

**Logic**:
1. Load current prompt; compute `previous_hash`.
2. If non-empty and new text differs: pause for HITL with summary
   (workspace-wide warning, length before/after, reason if provided,
   fingerprint of previous text).
3. On approve → re-read persona; if hash mismatch → conflict tool result
   (no write); else `set_system_prompt`.
4. On deny / cancel / timeout → tool result “not updated”.

Empty previous → write immediately (no HITL).

**Tests**: e2e or integration with HITL channel mock — confirm path commits;
deny does not; double-approve / concurrent UI edit yields conflict; custom-tool
HITL path does not raise durability error.

---

## Unit 4: Prompt guidance

**Files**:
- `backend/cubeplex/prompts/persona.py` (new short fragment) **or** extend
  memory authoring block carefully (prefer separate small section to avoid
  bloating memory rules)
- Inject from `run_manager` or a tiny middleware — **stable prefix**, short

**Copy must cover**:
- When to use `persona_*` vs `memory_*`
- Persona is workspace-wide (all members)
- Confirm before large rewrites (and tool enforces it)
- Never put secrets in persona
- Max 8000 characters
- Change applies on subsequent turns

**Cache note**: document that persona **content** changes bust cache; the
**guidance fragment** itself should be stable text.

---

## Unit 5: Verify next-turn assembly

**Files**: read-only check of `run_manager` load path; add regression test if
any caching is found.

**Test**: e2e — set persona via tool → start new run → assembled system prompt
contains new text (inspect via trace helper or internal test hook).

---

## Unit 6: Frontend tool result + settings refresh

**Files**:
- Tool result presentation for `persona_update` / `persona_get` (compact card)
- `PersonaEditor` / `workspaceSettingsStore` — refetch on window focus or
  explicit invalidate after tool event if already wired for other settings

**Tests**: component smoke if patterns exist; otherwise manual QA note in PR.

---

## Unit 7: User docs (implementation PR)

- Short note under workspace settings / persona guide: ask the agent to update
  persona; changes affect the whole workspace.

---

## Delivery order

1. Unit 1 (service extract)
2. Units 2+3+4 **together** (tools + HITL + prompt) — inseparable release
3. Unit 5 (assembly proof)
4. Unit 6 (UI polish)
5. Unit 7 (docs site)

## Out of scope

- append/search-replace operations
- admin-only policy flag
- audit columns
- renaming Persona UI label
- persona writes from scheduled/IM/automated runs (v1)

## Risks

| Risk | Mitigation |
| --- | --- |
| Agent overwrites long persona silently | Tool-enforced HITL when non-empty; no Phase-1-only ship |
| HITL from custom tool durability | Design against `allow_inside_custom_tool` / ask path |
| Stale confirm overwrites newer UI edit | previous_hash check at commit |
| Shared workspace surprise | Confirm + tool description |
| Automation mutates persona | Omit write tool on non-interactive tool lists |
| Cache cost after update | Accept; document; rare |
| Settings page stale | Refetch on focus |
