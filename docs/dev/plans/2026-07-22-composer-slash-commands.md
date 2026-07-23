# Composer Slash Commands — Implementation Plan

**Goal:** Add a `/`-triggered command palette on the chat composer with a
registry and P0 commands (`help`, `new`, `stop`, `model`, `effort`,
`rename`, `share`, `attach`).

**Architecture:**

```
InputBar (textarea)
   │ draft starts with /
   ▼
parseCommandToken(draft) → { open, query } | null
   │
   ▼
filterCommands(registry, query, ctx)
   │
   ▼
CommandPopover (listbox above input)
   │ Enter / click
   ▼
command.run(ctx) → clear slash token; no send()
```

Pure functions for parse/filter live in `lib/slash-commands/` for unit
tests without mounting the full composer. `InputBar` owns open state,
highlight index, and builds `SlashCommandContext` from existing hooks
(workspace, message store, model picker refs, file input, router).

**Tech stack:** React 19, Next.js, Zustand (`@cubeplex/core`), next-intl,
lucide-react, Vitest + RTL; optional Playwright smoke.

**Spec:** [docs/dev/specs/2026-07-22-composer-slash-commands-design.md](../specs/2026-07-22-composer-slash-commands-design.md)  
**Issue:** #390

---

## File structure

| File | Action | Responsibility |
| --- | --- | --- |
| `frontend/packages/web/lib/slash-commands/types.ts` | Create | `SlashCommand`, `SlashCommandContext` |
| `frontend/packages/web/lib/slash-commands/parse.ts` | Create | Detect leading `/` token + query |
| `frontend/packages/web/lib/slash-commands/filter.ts` | Create | Filter + availability |
| `frontend/packages/web/lib/slash-commands/registry.ts` | Create | P0 command definitions |
| `frontend/packages/web/lib/slash-commands/index.ts` | Create | Barrel |
| `frontend/packages/web/components/chat/CommandPopover.tsx` | Create | Popover UI + keyboard list |
| `frontend/packages/web/components/layout/InputBar.tsx` | Modify | Wire parse, popover, keydown, run |
| `frontend/packages/web/messages/en.json` / `zh.json` | Modify | `slashCommands.*` |
| `frontend/packages/web/lib/slash-commands/__tests__/*.ts` | Create | parse/filter/registry availability |
| `frontend/packages/web/__tests__/components/CommandPopover.test.tsx` | Create | keyboard + select |
| `frontend/packages/web/__tests__/components/InputBar.slash.test.tsx` | Create | open / run / no-send |
| `docs/site/docs/…` (implementation PR) | Create/update | User-facing command list |

Exact docs path follows
[docs/dev/plans/2026-06-23-docs-overhaul.md](./2026-06-23-docs-overhaul.md)
mapping when implementation ships.

---

## Unit of work 1 — Parse + filter pure helpers

**Files:** `parse.ts`, `filter.ts`, unit tests

**Interfaces:**

```ts
type CommandToken =
  | { kind: 'command'; raw: string; query: string }
  | null

function parseLeadingCommandToken(draft: string): CommandToken
// MUST match spec §4.1: open only when draft matches /^\s*\/(\S*)$/
// "/foo bar", multiline, mid-draft "/" → null (palette closed).

function filterCommands(
  commands: SlashCommand[],
  query: string,
  ctx: SlashCommandContext,
): SlashCommand[]
```

**Core logic:**

- Trigger only for a draft that is **only** the command token
  (optional leading whitespace + `/` + optional non-space query). This
  matches “start of draft” and avoids mid-sentence `/`.
- Filter: case-insensitive includes on `name`, `aliases`, `keywords`.
- Drop commands where `isAvailable(ctx)` is false (MVP hide).

**Tests (intent):**

- `""`, `"hello"`, `" /nope mid"` → null / not open as appropriate
- `"/"` → open, query `""`
- `"/mod"` → query `"mod"`; filters to model
- `"/stop"` available only when `ctx.isStreaming`
- Zero matches returns `[]`

---

## Unit of work 2 — Registry (P0)

**Files:** `registry.ts`, `types.ts`

**Core logic:** Implement the eight P0 commands from the spec:

| id | run sketch |
| --- | --- |
| `help` | Set popover to help mode or clear query and show all |
| `new` | `ctx.createNewChat()` — same as existing new-chat entry |
| `stop` | `ctx.cancelStream(conversationId)` |
| `model` | `ctx.openModelPicker()` |
| `effort` | `ctx.openEffortControl()` |
| `rename` | `ctx.startRename()` — may navigate focus to title/sidebar rename; inject callback from shell if needed |
| `share` | `ctx.openShare()` — open existing share panel control |
| `attach` | `ctx.openAttach()` — `fileInputRef.click()` |

Wire **callbacks from InputBar/shell** rather than importing half the app
into the registry module. Registry holds declarative metadata + thin
`run` that only uses `ctx`.

**Availability:**

- `stop`: `ctx.isStreaming && !!ctx.conversationId`
- `rename` / `share`: `!!ctx.conversationId`
- `effort`: when effort UI is applicable (reuse same conditions as
  composer effort control visibility)

**Tests:** table-driven availability for streaming vs idle, with/without
conversation id.

---

## Unit of work 3 — CommandPopover UI

**Files:** `CommandPopover.tsx`

**Interfaces:**

```ts
type CommandPopoverProps = {
  open: boolean
  commands: SlashCommand[]
  activeIndex: number
  onActiveIndexChange: (i: number) => void
  onSelect: (cmd: SlashCommand) => void
  onClose: () => void
  // labels via useTranslations('slashCommands')
}
```

**Core logic:**

- Position above composer (absolute/fixed within InputBar relative root).
- Render name as `/name`, description from i18n.
- Roles: `listbox` + `option`, `aria-activedescendant`.
- Empty: “No commands” string.

**Tests:** renders rows; click calls `onSelect`; a11y roles present.

---

## Unit of work 4 — InputBar integration

**Files:** `InputBar.tsx`

**Core logic:**

1. On content change, `parseLeadingCommandToken(content)` → set
   `slashOpen` + `slashQuery`.
2. Build `ctx` with workspace id, streaming flags, handlers:
   - stop → existing `cancelStream`
   - attach → existing file input
   - model/effort → expose open state or imperative handle on
     `ModelPicker` if needed (minimal prop: `modelPickerOpen` /
     `requestOpenModelPicker` — surgical, avoid large ModelPicker rewrite)
   - new → existing `onCreateConversation` / router to workspace home
   - share → call same control AppShell uses for SharePanel if reachable;
     otherwise navigate or dispatch a small UI event already used for share
   - rename → conversation store rename entry or focus title editor
3. `onKeyDown` when slash open:
   - ArrowUp/Down: preventDefault, move index
   - Enter/Tab with ≥1 match: preventDefault, `run` selected (or first)
   - Escape: close palette, keep draft
   - Enter with 0 matches: **fall through to existing Enter matrix**
     (`steer` if streaming+text, else `send`) — never invent a third path
   - Respect `e.nativeEvent.isComposing` (no-op) like today
4. After successful `run`, clear content (or strip token) and close
   palette — **do not** call `send` / `steer` with slash text.
5. Ensure normal send/steer still works when palette closed.
6. **P0 shell seams (same PR as wiring):** implement the minimum APIs in
   spec §4.7.1 (`ModelPicker` open, `SharePanel` open callback into
   InputBar, rename action for current conversation). Commands without a
   seam stay `isAvailable: false` / hidden — no silent no-op in the list.

**Tests (intent):**

- Type `/` → popover shows `/new` (or help list).
- Select `/stop` while streaming mock → `cancelStream` called; no
  `send`/`steer`.
- `/zzzzz` + Enter idle → `send` path; streaming → `steer` path.
- `/foo bar` does not open palette.
- Esc closes without send.
- `/new` while streaming does not call `cancelStream`.

---

## Unit of work 5 — i18n + user docs (implementation PR)

**Files:** `en.json`, `zh.json`, `docs/site/docs/...`

- All command titles/descriptions under `slashCommands`.
- User doc page listing P0 commands when feature merges (project rule:
  user-facing change updates docs site in same PR).

---

## Out of scope this plan

- P1 open-surface commands (unless a single `router.push` is free)
- Global Cmd+K
- Dynamic skill registration
- Backend changes

---

## Verification

```bash
cd frontend/packages/web
pnpm exec vitest run lib/slash-commands __tests__/components/CommandPopover.test.tsx __tests__/components/InputBar.slash.test.tsx
```

Optional Playwright: type `/` in chat → see `/new` → run.

---

## Suggested implementation commits

1. `feat(web): add slash command registry and parse/filter helpers`
2. `feat(web): CommandPopover + InputBar slash integration (P0)`
3. `docs(site): document composer slash commands`

Implementation waits for design approval.
