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
- Surfaces already in product (deep-link / open targets for later phases):
  skills, MCP, memory, artifacts, scheduled tasks, triggers, sandbox env,
  share panel (`SharePanel` in `AppShell`), conversation search, rename
  via conversation store.
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
  **Help** (light grouping; skip if cluttered).
- Filter: case-insensitive **substring** on name, aliases, and keywords.
- Keyboard: ↑/↓ move highlight, Enter/Tab apply, Esc dismiss without
  sending.
- Mouse: click row applies.
- a11y: `listbox` / `option`, `aria-activedescendant`, focus remains in
  textarea where practical.

### 4.4 Execution semantics

| Kind | Behavior | P0 examples |
| --- | --- | --- |
| **Client action** | Run immediately; remove `/…` from composer; **no** user message bubble | `/new`, `/stop`, `/rename`, `/attach` |
| **Open control** | Focus/open existing UI chrome | `/model`, `/effort` |
| **Open surface** | Navigate or open panel (P1) | `/skills`, `/mcp`, … |

Running a client command must **not** call `send` with the slash text.

### 4.5 Command registry

Frontend module, e.g.
`frontend/packages/web/lib/slash-commands/registry.ts` (+ types):

```ts
type SlashCommandContext = {
  conversationId?: string
  workspaceId: string | null
  isStreaming: boolean
  // All open-* / navigation actions are **injected by InputBar/shell** —
  // registry never imports AppShell / Sidebar.
  cancelStream: (conversationId: string) => void
  openModelPicker: () => void
  openEffortControl: () => void
  startRename: () => void
  openAttach: () => void
  createNewChat: () => void | Promise<void>
  openShare: () => void
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

**P1+** (registry stubs allowed, not required in Phase 1): `/skills`,
`/mcp`, `/memory`, `/sandbox`, `/artifacts`, `/search`, `/topic`,
`/schedule`, `/triggers`, dynamic skills, `/compact`, `/fork`, etc. —
see issue body. Do not implement full P1 in the first implementation PR
unless already trivial deep links.

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

### 4.7.1 Minimum concrete shell APIs for P0 open-controls (required)

Today these are **not** callable from `InputBar` alone; implementation must
add thin, explicit seams (choose the smallest existing pattern):

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
  least `/new`, `/help`, `/model`, `/effort`, `/attach` where controls
  exist; commands needing `conversationId` stay unavailable/hidden.

## 5. Out of scope (MVP)

- Global Cmd/Ctrl+K palette.
- User-defined custom slash macros.
- `@` mentions / file reference pickers (reserve `@` for later).
- Replacing IM bot command systems.
- Registering every installed skill as `/skill-name` (Phase 3).
- Org-admin deep links.
- Notifications / unread / running indicators (#388, #389).

## 6. Success criteria

1. Typing `/` at draft start opens the command list without sending.
2. Filtering narrows; ↑/↓ / Enter / Esc / click work.
3. All **P0** commands implemented with correct enablement (`/stop` only
   while streaming).
4. Client commands do not create a user message bubble for slash text.
5. Unavailable commands hidden or disabled with a clear rule.
6. i18n keys + basic listbox a11y.
7. Unknown `/text` + Enter with zero matches sends as plain text (tested).
8. User docs list shipped commands when the **implementation** merges
   (not required for this design PR).

## 7. Phasing

| Phase | Deliverable |
| --- | --- |
| **1** (this issue MVP) | Palette + registry + P0 |
| **2** | P1 open-surface deep links |
| **3** | Dynamic skills + custom templates + `/compact` / `/fork` when supported |

## 8. Open questions (resolved)

| Question | Decision |
| --- | --- |
| `/` mid-draft? | No — whole draft must be `/query` token only |
| Space after `/`? | Closes palette (escape to plain text) |
| Zero-match Enter while streaming? | Same as normal Enter → steer |
| `/model` UX? | Open existing `ModelPicker` via controlled open seam |
| `/new` while streaming? | Allow without auto-stop; stream ownership unchanged |
| Include P1 in MVP? | No — P0 only; registry ready for P1 |
| Skills as dynamic entries? | Phase 3 |
| Missing shell seam for a P0 command? | Hide command until seam exists in same PR |
