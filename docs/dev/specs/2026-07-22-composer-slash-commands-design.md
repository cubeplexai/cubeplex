# Composer Slash Commands — Design

**Status:** Draft  
**Date:** 2026-07-22  
**Related:** #390 · related UX #388 / #389 (not in scope)

## 1. Goal

Typing `/` in the chat composer opens a filterable command palette
(Slack / Discord / Cursor / Claude Code style): name + short description,
keyboard navigation, Enter to run or apply. Ship a curated **P0** command
set that maps to real product actions, with a clean registry for later
skills and open-surface commands.

## 2. Context

### What exists today

- Composer: `frontend/packages/web/components/layout/InputBar.tsx` —
  free-text textarea, attachments, model picker, send / stop / steer.
  No slash parsing.
- Stream control: `useMessageStore.send`, `cancelStream`, `steer`.
- Model / effort: `ModelPicker` + per-workspace preset selection store
  (`lib/stores/preset-selection.ts`).
- Surfaces already in product (deep-link / open targets):
  skills (`/w/{wsId}/skills`), MCP (`/w/{wsId}/mcp`), memory, artifacts,
  scheduled tasks, triggers, sandbox env, share panel (`SharePanel` in
  `AppShell`), conversation search, rename via conversation store.
- Context compaction: automatic via `CompactionMiddleware` on agent turns;
  no user-facing “compact now” control yet (needed for `/compact`).
- Empty-state `PromptCards` + `useComposerDraft` for prompt injection —
  complementary discovery, not a command system.
- IM bots have separate platform slash semantics in backend docs; **web
  composer commands are a separate registry**.

### Why change

Power users and new users both lack a keyboard-first way to discover
actions without hunting menus. Competitive agent products treat `/` as the
primary in-composer command entry.

## 3. Approaches considered

| Approach | Pros | Cons |
| --- | --- | --- |
| **A. Composer-scoped `/` + frontend command registry** (recommended) | Matches issue; reuses InputBar; no backend; extension point for skills later | Not a global Cmd+K palette |
| **B. Global app command palette (Cmd/Ctrl+K) first** | Power-user everywhere | Broader UX; issue defers this; can reuse registry later |
| **C. Send `/foo` as user text and let the agent interpret** | Zero UI | Unreliable; pollutes history; not real client actions |

**Recommendation: A**, Phase 1 = palette UX + registry + **P0 only**.

## 4. Design

### 4.1 Trigger rules

**Single grammar (resolved — spec and plan must match):**

Open the palette only when the **entire draft** is a single leading
command token:

```text
/^\s*\/(\S*)$/
```

| Draft | Palette | Query |
| --- | --- | --- |
| `/` | open | `""` |
| `/mod` | open | `"mod"` |
| `  /stop` | open | `"stop"` |
| `/foo bar` | **closed** (space ended the token) | — |
| `/model\nextra` | **closed** (newline ends “single token” draft) | — |
| `hello /mod` | **closed** (not start-only whole draft) | — |
| mid-sentence `/` | **closed** | — |

- Query = capture group 1 (text after `/`, no spaces).
- **Close when:** Esc, click outside, successful `run` that clears the
  draft/token, or draft no longer matches the regex (including space or
  more prose after the token — this is the literal-`/` escape).
- Caret position: MVP ignores caret and uses full draft value only (no
  mid-field open). IME: ignore key handling while `e.nativeEvent.isComposing`
  (same as InputBar today).
- Paste: same regex on the resulting value.

### 4.2 Unknown `/text` + Enter (resolved)

- Palette open with ≥1 match: Enter / Tab applies **highlighted** row
  (default first match); **do not** send/steer.
- Palette open with **0** matches: fall through to the **normal Enter
  path** for the current composer mode:
  - **Idle:** `send` as plain text (Slack-like).
  - **Streaming on this conversation:** existing behavior is **steer**
    when the box has text — zero-match slash drafts use that same path
    (steer the live run with the literal text). Do **not** invent a
    separate “force new message” path for MVP.
- Escape for literal leading slash: type space after `/` (palette closes)
  then continue typing, or send/steer with zero matches.

### 4.3 Popover UX

- Anchored **above** the `InputBar` textarea.
- Rows: optional icon | `/name` | short description; optional right badge
  (“Streaming only”).
- Optional sections for P0: **Conversation**, **Run**, **Composer**,
  **Tools** (`/skills`, `/mcp`, `/compact`), **Help** (light grouping;
  skip if cluttered).
- Filter: case-insensitive **substring** on name, aliases, and keywords.
- Keyboard: ↑/↓ move highlight, Enter/Tab apply, Esc dismiss without
  sending.
- Mouse: click row applies.
- a11y: `listbox` / `option`, `aria-activedescendant`, focus remains in
  textarea where practical.

### 4.4 Execution semantics

| Kind | Behavior | P0 examples |
| --- | --- | --- |
| **Client action** | Run immediately; remove `/…` from composer; **no** user message bubble | `/new`, `/stop`, `/rename`, `/attach`, `/compact` |
| **Open control** | Focus/open existing UI chrome | `/model`, `/effort` |
| **Open surface** | Navigate or open panel | `/skills`, `/mcp`, `/share` |

Running a client command must **not** call `send` with the slash text.

### 4.5 Command registry

Frontend module, e.g.
`frontend/packages/web/lib/slash-commands/registry.ts` (+ types):

```ts
type SlashCommandContext = {
  conversationId?: string
  workspaceId: string | null
  isStreaming: boolean
  // All open-* / navigation / compact actions are **injected by
  // InputBar/shell** — registry never imports AppShell / Sidebar.
  cancelStream: (conversationId: string) => void
  openModelPicker: () => void
  openEffortControl: () => void
  startRename: () => void
  openAttach: () => void
  createNewChat: () => void | Promise<void>
  openShare: () => void
  openSkills: () => void
  openMcp: () => void
  /** Force context compaction for the active conversation. */
  compactConversation: (conversationId: string) => void | Promise<void>
}

type SlashCommand = {
  id: string
  name: string            // without leading slash, e.g. "new"
  aliases?: string[]
  descriptionKey: string  // next-intl key
  category: 'conversation' | 'run' | 'composer' | 'help' | 'tools'
  keywords?: string[]
  isAvailable: (ctx: SlashCommandContext) => boolean
  /** Optional reason key when unavailable (for disabled row) */
  unavailableReasonKey?: (ctx: SlashCommandContext) => string | null
  run: (ctx: SlashCommandContext) => void | Promise<void>
}
```

Filter API: pure function
`filterCommands(commands, query, ctx) → visible list`
(available first; unavailable either hidden or disabled — **MVP: hide
unavailable** for less clutter, except `/stop` which is simply absent when
not streaming).

### 4.6 P0 command catalog (ship)

| Command | Kind | Behavior | Availability |
| --- | --- | --- | --- |
| `/help` | Client | Show full command list in the same popover (clear filter / help mode) or a small dialog listing P0 commands | Always |
| `/new` | Client | Same as sidebar “New Chat” (navigate to new / call existing create flow) | Always |
| `/stop` | Client | `cancelStream(conversationId)` — same path as stop button | Only while this conversation is streaming |
| `/model` | Open control | Open / focus existing `ModelPicker` | Always when composer shows model picker |
| `/effort` | Open control | Open or cycle thinking/effort control when model supports it; if unsupported, hide or no-op with toast | When effort control is meaningful |
| `/rename` | Client | Enter rename flow for current conversation (reuse sidebar rename or title edit entrypoint) | When `conversationId` present |
| `/share` | Open surface | Open existing share UI for current conversation | When `conversationId` present |
| `/attach` | Client | Trigger paperclip / file input (`fileInputRef.click()`) | Always (composer attach) |
| `/skills` | Open surface | Navigate to workspace skills page (`/w/{wsId}/skills`) | When `workspaceId` present |
| `/mcp` | Open surface | Navigate to workspace MCP / connectors page (`/w/{wsId}/mcp`) | When `workspaceId` present |
| `/compact` | Client | Force context compaction for the current conversation (summarize older turns so later model calls use less prompt). **No** user message bubble for `/compact` itself. Show a short success/error toast (or inline status) from the API result. | When `conversationId` present and **not** streaming |

#### 4.6.1 `/compact` product semantics

Today compaction only runs automatically inside the agent turn
(`CompactionMiddleware` when token thresholds trip). `/compact` is the
**manual** path (Claude Code / Codex analogue):

1. User runs `/compact` while idle on a conversation.
2. Client calls a thin force-compact API (see §4.7.1) — **not** `send` of
   the string `/compact`.
3. Backend reuses the existing compaction pipeline (same summary model /
   task routing as automatic compaction) against the conversation’s
   checkpointer state, even if the ratio threshold has not been reached.
4. UI history stays intact (compaction already does not rewrite
   `state.messages` for the transcript); only model-facing projection
   shrinks.
5. While streaming: command is **hidden** (same rule as other run-sensitive
   actions). Do not race compact with an active SSE run.

If the force-compact endpoint cannot land in the same implementation PR as
the palette, hide `/compact` (`isAvailable: false`) until the seam exists —
no silent no-op row.

**P1+** (registry stubs allowed, not required in Phase 1): `/memory`,
`/sandbox`, `/artifacts`, `/search`, `/topic`, `/schedule`, `/triggers`,
dynamic skills, `/skill` picker, `/fork`, etc. — see issue body.

### 4.7 `/new` while streaming (resolved)

**Allow** navigate/create without auto-stop (ties to #388 / #389 background
awareness). Do not force-cancel the previous run from `/new`.

**Lifecycle after `/new` (resolved for implementers):**

- Reuse the same code path as the existing “New Chat” control in the shell
  (navigate to workspace home / create draft conversation — whichever that
  control already does on that surface).
- Do **not** clear global `isStreaming` / `streamingConversationId` just
  because the user left the page; single-stream ownership stays with the
  conversation that owns the SSE until cancel or a new `send` aborts it.
- Home empty composer: `/new` may no-op or re-focus empty state if already
  there; conversation page: same as header/sidebar new-chat.
- When the user later `send`s from the new conversation, existing
  `activeStreamController.abort()` rules apply (one active client stream).

### 4.7.1 Minimum concrete shell APIs for P0 commands (required)

Today several of these are **not** callable from `InputBar` alone;
implementation must add thin, explicit seams (choose the smallest existing
pattern):

| Command | Required seam |
| --- | --- |
| `/model` | Controlled open prop or `requestOpen()` on `ModelPicker` (Popover is uncontrolled today) |
| `/effort` | Same visibility/open control the composer effort UI already uses; hide command when effort UI is not shown |
| `/share` | Lift `SharePanel` open state to `AppShell` context/callback and pass `openShare` into `InputBar` |
| `/rename` | Export a store action or shell event that enters rename for `activeId` / `conversationId` (row-local `isEditing` is not enough) |
| `/attach` | Existing `fileInputRef.click()` in `InputBar` |
| `/stop` | Existing `cancelStream` |
| `/new` | Existing `onCreateConversation` and/or router path used by New Chat |
| `/help` | Local popover mode only |
| `/skills` | `router.push(\`/w/${workspaceId}/skills\`)` (existing page) via `ctx.openSkills` |
| `/mcp` | `router.push(\`/w/${workspaceId}/mcp\`)` (existing page) via `ctx.openMcp` |
| `/compact` | `ctx.compactConversation(id)` → client helper calling a **force-compact** API on the conversation (thin endpoint that reuses `CompactionMiddleware` / summarizer path). Hide until API + helper exist. |

Silent no-ops are **not** acceptable for P0 commands that appear in the
list — if a seam is missing, the command must be `isAvailable: false`
(hidden) until the seam lands in the same implementation PR.

### 4.8 i18n

- Namespace e.g. `slashCommands.*` in `en.json` / `zh.json`:
  - per-command `title` / `description`
  - `noMatches`, `helpHeading`, a11y strings
- English-first for external docs when the feature ships
  (`docs/site/docs/…` composer commands page — **with implementation PR**,
  not this design-only PR).

### 4.9 Integration point

- Primary: `InputBar` textarea `onChange` / `onKeyDown`.
- Extract `CommandPopover` component under
  `frontend/packages/web/components/chat/` or `layout/`.
- Home/empty composer (`onCreateConversation` path) should support at
  least `/new`, `/help`, `/model`, `/effort`, `/attach`, `/skills`,
  `/mcp` where controls / `workspaceId` exist; commands needing
  `conversationId` (`/rename`, `/share`, `/compact`, `/stop`) stay
  unavailable/hidden.

## 5. Out of scope (MVP)

- Global Cmd/Ctrl+K palette.
- User-defined custom slash macros.
- `@` mentions / file reference pickers (reserve `@` for later).
- Replacing IM bot command systems.
- Registering every installed skill as `/skill-name` (Phase 3).
- Org-admin deep links (`/admin/skills`, `/admin/mcp` — workspace pages only).
- Notifications / unread / running indicators (#388, #389).
- Changing automatic compaction thresholds / policy (only add a manual
  force path for `/compact`).

## 6. Success criteria

1. Typing `/` at draft start opens the command list without sending.
2. Filtering narrows; ↑/↓ / Enter / Esc / click work.
3. All **P0** commands implemented with correct enablement (`/stop` only
   while streaming; `/compact` only idle + conversation; `/skills` /
   `/mcp` only with workspace).
4. Client commands do not create a user message bubble for slash text
   (including `/compact`).
5. Unavailable commands hidden or disabled with a clear rule.
6. i18n keys + basic listbox a11y.
7. Unknown `/text` + Enter with zero matches sends as plain text (tested).
8. User docs list shipped commands when the **implementation** merges
   (not required for this design PR).
9. `/skills` / `/mcp` land on the existing workspace pages; `/compact`
   either force-compacts via API or stays hidden until that API ships.

## 7. Phasing

| Phase | Deliverable |
| --- | --- |
| **1** (this issue MVP) | Palette + registry + full P0 (incl. `/skills`, `/mcp`, `/compact`) |
| **2** | Remaining P1 open-surface deep links (`/memory`, `/sandbox`, …) |
| **3** | Dynamic skills + custom templates + `/fork` when supported |

## 8. Open questions (resolved)

| Question | Decision |
| --- | --- |
| `/` mid-draft? | No — whole draft must be `/query` token only |
| Space after `/`? | Closes palette (escape to plain text) |
| Zero-match Enter while streaming? | Same as normal Enter → steer |
| `/model` UX? | Open existing `ModelPicker` via controlled open seam |
| `/new` while streaming? | Allow without auto-stop; stream ownership unchanged |
| Include extra open-surface / compact in P0? | **Yes** — `/skills`, `/mcp`, `/compact` are P0 (promoted from issue P1/P2) |
| Skills as dynamic entries? | Phase 3 |
| Missing shell seam for a P0 command? | Hide command until seam exists in same PR |
| `/compact` while streaming? | Hidden; do not race active run |
