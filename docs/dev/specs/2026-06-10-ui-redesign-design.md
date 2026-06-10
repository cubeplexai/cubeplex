# UI Redesign — Design Spec

**Date:** 2026-06-10
**Status:** Approved (brainstormed with user, direction validated via hi-fi prototypes)
**Scope:** Full product — chat interface, workspace management pages, admin console

## Problem

The current UI reads as amateur. A frontend audit (all 31 pages, 199 component
files) found:

- ~127 distinct hardcoded color utility classes across 44 component files
  (`bg-amber-500/10`, `border-blue-200`, `bg-green-600`, …) plus widget hex
  literals, all bypassing the theme tokens
- No consistent spacing, radius, or type scale (`text-[0.8rem]`, `text-[11px]`,
  `text-[9px]`; `rounded-md/lg/xl/2xl` mixed freely)
- Missing interaction states: weak/absent hover, focus, active feedback;
  inconsistent loading (spinner vs skeleton), missing empty/error states
- Saturated blue user bubbles dominating the chat; assistant avatar noise
- Admin console visually disconnected from the main app (looks like a second
  product)
- 23 right-panel components with at least 6 different header styles
- No mobile support anywhere

(Note: the "overlapping avatars" seen in dev screenshots is the Next.js dev
tools overlay, not a product bug — no fix needed.)

## Direction

**"Vercel Mono"** — selected by the user from three hi-fi prototypes
(Linear Precision / Vercel Mono / Graphite & Signal). Character: pure-black
base, crisp 1px borders for layering (not shadows), one restrained blue
accent, small radii, heavy use of monospace for data/commands/paths.

Style anchor only; **interaction details follow the real current app**, except
where this spec explicitly changes them.

### Decisions made with the user

| Decision | Choice |
|---|---|
| Scope | Everything, one initiative |
| Dark/light priority | Dark-first design target; light must reach equal quality; default follows system |
| Interaction depth | Micro-interactions + state completeness, interaction-pattern refactors, motion system. No command palette this round |
| Mobile | Chat interface only; management pages get non-breaking fallback |
| User bubble | Desaturated: `#111` bg + border, no saturated blue |
| Input bar | V1 "integrated toolbar"; model preset + thinking selectors right-aligned in the internal bottom bar; attach on the left |
| Input placeholder | Task-oriented: "Describe a task…" (the old "How can I help you?" had reversed tone — the user commands the agent) |
| Empty-state home | Keep the logo + name lockup (mark itself becomes the placeholder); add 3 clickable example-task prompt cards |
| Logo | Out of scope; use a simple geometric placeholder mark |
| CJK fonts | Geist falls back to `PingFang SC / Microsoft YaHei / Noto Sans CJK` |

## 1. Design tokens (foundation layer)

All values live as CSS variables in `globals.css` (Tailwind 4 `@theme`).
Component code references tokens only — no raw color values anywhere after
this initiative.

**Color (dark, primary target)**

- Backgrounds, 4 layers: base `#000`, panel `#0a0a0a` (sidebar, admin nav),
  raised `#111` (input bar, user bubble, hover surfaces), sunken `#050505`
  (code blocks, terminal)
- Borders: `#1f1f1f` default, `#333` strong. Layering comes from borders,
  not shadows
- Text: `#ededed` primary / `#a1a1a1` secondary / `#666` faint
- Accent: `#0070f3`, used only for: primary action buttons, focus rings,
  active indicators. Everything else is grayscale
- Semantic: four statuses — success / warning / danger / **info** (info
  covers awaiting-input surfaces, informational notices, "official" badges —
  today's ad-hoc blues that are NOT actions; the accent stays
  action-only). Each status is a small set, not one flat value:
  `surface` / `border` / `fg` / `solid` — real components need all four
  (e.g. SandboxConfirmCard uses five amber shades on one card today).
  Replaces every ad-hoc `amber/green/red/blue` utility in components
- Light mode mirrors the same token structure (white base, `#eaeaea`
  borders, same accent), polished to equal quality
- Hex values above are direction targets validated in the prototype; final
  values are tuned in `globals.css` during the token PR (contrast, light
  mode). `globals.css` is the source of truth after that PR — do not
  back-port tuned values into this spec

**Theme default migration** (current code: `defaultTheme="light"`,
`enableSystem={false}` in `app/layout.tsx`; both toggles do a binary
`theme === 'dark' ? 'light' : 'dark'` flip):

- Enable system preference; new users default to `system`
- Toggles must read `resolvedTheme` (not `theme`) or the first click is a
  visual no-op for system-dark users
- Users with a stored explicit preference keep it (acceptable; no
  migration of localStorage values)
- The toggle stays binary (light/dark); choosing it simply leaves system
  mode — no third "system" option in the UI this round

**Typography**

- UI + body: Geist Sans (replaces IBM Plex Sans); code/data/commands:
  Geist Mono
- Semantic size scale: 11 / 12 / 13 / 14 / 16 / 20 / 24 px — replaces all
  arbitrary `text-[…]` values
- Numbers in data contexts (tokens, cost, timestamps): `tabular-nums`
- Section labels: 11px uppercase with letter-spacing

**Shape & spacing**

- Radius, 3 steps: 4px (badges, chips) / 6px (buttons, inputs, cards) /
  10px (panels, modals)
- Spacing on a 4px grid; standard values for page header height, section
  gaps, card padding

**Motion**

- Durations: `--duration-fast` 120ms (hover/press), `--duration-base` 200ms
  (expand/fade), `--duration-slow` 300ms (panel slides)
- Easing: `cubic-bezier(0.16, 1, 0.3, 1)` (ease-out family)
- CSS-only — no animation library added. `transform` + `opacity` only.
  Respect `prefers-reduced-motion`

## 2. Chat interface

Information architecture unchanged: sidebar (`w-56`, 224px — unchanged) /
main column / resizable right panel.

**Sidebar**

- Panel background `#0a0a0a` + 1px right border to separate from `#000` main
- Active conversation: 2px left indicator bar; hover uses the standard
  hover token (defined once in the token layer, used everywhere)
- Group labels (Pinned/Today/…): 11px uppercase + tracking
- Account footer restyled: avatar + email (no plan badge — the user model
  has no plan field and backend changes are a non-goal); same account
  component reused in the admin top bar

**Message stream**

- Content column max-width 760px, centered
- User message: raised bg, default border token, 6px radius,
  right-aligned, max-width 78%
- Assistant message: no bubble, no avatar — pure typography
- Tool-call group: bordered container of compact mono rows (icon + tool
  name + arg summary + state). Running = spinner, done = green check,
  failed = red cross. Row click opens the right panel (same semantics as
  today)
- Metadata chips (Thinking, Token Usage): 11px mono low-contrast chips on
  one row at message end
- Code blocks: filename header bar + copy button, `#050505` bg

**Input bar (V1)**

- Raised bg + strong border; blue focus ring on focus-within
- Internal bottom toolbar: attach left; preset + thinking selectors
  right-aligned next to the send button
- Placeholder: "Describe a task…" (zh: "描述一个任务…" — both locales
  authored together; i18n key parity is pre-commit enforced). The
  placeholder stays dynamic: the existing HITL-lock variant
  (`pendingHitlLock`) is kept. NOTE: 13 E2E usages across 7 spec files
  locate the input via `getByPlaceholder('How can I help you?')` (plus a
  zh assertion in i18n.spec.ts) — these text selectors must be updated in
  the same PR as the copy change
- **Streaming semantics are preserved exactly as today** (this spec
  restyles, it does not change them): stop button shows only while
  streaming AND the box is empty; typing mid-stream flips the button back
  to send, which steers the live run (Enter mid-stream also steers); the
  PendingSteers chip stack and attachment chips / upload dropzone above
  the input remain part of the input zone
- Send button: solid accent; clear disabled state

**Right panel — unified Panel Shell**

Today: 23 panel components, ≥6 header styles. A shell + adapter
architecture already half-exists: `ToolDetailPanel` dispatches 7 content
types under one `PanelHeader`. The actual fragmentation seam is one level
up — `AppShell` switches between 4 sibling panels (ToolDetailPanel,
ArtifactPanel, BrowserView, SkillCandidatePanel/AttachmentPreview) that
each hand-roll their own header (ArtifactPanel copy-pastes PanelHeader's
markup). Therefore: **extend, don't rebuild**:

- Generalize `PanelHeader` (today its props are tool-coupled:
  toolName/toolArgs/toolResult) into the shared shell header: icon +
  title + mono subtitle (command/path) + standard actions (copy /
  fullscreen / close) + **a per-adapter action slot** — ArtifactPanel
  needs its version popover + download link, BrowserView needs
  take-over/hand-back + refresh; without the slot, special panels fork
  the header again and we recreate today's divergence inside the new
  abstraction
- Fold the 4 sibling panels under the generalized shell; the existing
  ToolDetailPanel adapter views (terminal, search, web fetch, file
  read/write diff, skill, generic) keep their dispatch pattern and are
  restyled in place
- Shared content container: padding, scroll, empty/loading/error states
- Switching content updates the header in place — no full-panel flash
- Terminal adapter: real terminal feel — darker bg, mono, ANSI color
  mapping
- This generalization is its own PR (see implementation strategy), not a
  clause inside the chat restyle PR — it touches panel open/close,
  content switching, and resize behavior, the highest regression surface
  in the app

**Empty-state home**

- Geometric placeholder mark (brand logo is a separate project)
- 3 prompt cards (example tasks: analyze a data file / research a topic /
  automate a workflow); click fills the input

**State completeness**

- Streaming: blinking end-of-line cursor; spinner on running tools
- Loading: skeletons shaped like the real layout (list + message history)
- Errors: RunErrorBubble restyled as an inline error bar (icon + reason).
  Display-only, as today — no retry action: the client has no
  re-run/resend mechanism and backend changes are a non-goal. If a retry
  mechanism lands later, the bar gains the action then
- Empty: the standard empty-state component is the EXISTING
  `components/shared/EmptyState.tsx` (same API: icon/title/description/
  action), restyled from its dashed `bg-muted/20` look to the new token
  language — do not build a second one

## 3. Management pages (workspace + admin)

One **management page layout language** applied to the ~24 management
pages (15 admin + 9 workspace; the setup flow and OAuth return pages are
excluded — they are flow pages, not management pages).

**Scope boundary (hard rule from CLAUDE.md):** "template" means a set of
shared modules — `PageHeader`, `ToolbarRow`, `MasterDetail`, `DangerZone`
— that each scope's own page file assembles. It is NOT a single
parameterized `ManagementPage` component: admin and workspace pages stay
separate files with no `mode`/`scope` props. Reuse lives at the module
level only.

- **Page header**: title (20px/600) + one-line description (13px secondary)
  + at most one solid accent button, right-aligned
- **Toolbar row**: search input + segmented filter control, one style
  everywhere. The segmented control is a restyle of the existing
  `components/ui/tabs.tsx` (already used as a filter switcher in 9+
  files) — not a new parallel widget
- **Master-detail**: list selection uses the same 2px left indicator
  language as the sidebar; detail empty state uses the standard empty-state
  component
- **Tables**: unified row height, alignment, hover, row-action visibility
- **Forms**: unified label/input/help/error vertical rhythm; destructive
  areas use a standard "Danger Zone" red-bordered block

**Admin relationship to main app**

- Admin keeps its own top bar (legitimate context switch) but uses the same
  token set — no more white-bar-vs-gray-sidebar split-brain
- Admin top bar redesigned:
  - Left: cubebox mark + `ADMIN` as an 11px uppercase tracked label + org
    name at 13px/500 primary, separated by `/` (path semantics)
  - Right: "Back to app" becomes a ghost button with border (← icon +
    label) as an explicit exit
  - Avatar: same account component as the sidebar footer (squircle avatar,
    same dropdown styling, danger items red)
- Admin left nav adopts the sidebar's visual language (item height, active
  indicator, group labels)

**Interaction-pattern refactors**

- Modals: simple forms (≤3 fields) stay dialogs; complex forms become
  slide-over panels from the right (list stays visible). The Models add
  wizard stays a full-page wizard. **New primitive required**: `ui/` has
  no sheet/drawer today — add `ui/sheet.tsx` (shadcn Sheet) in the UI
  primitives PR; every slide-over uses it (otherwise each area PR
  hand-rolls its own overlay and we recreate the fragmentation this spec
  exists to kill)
- Inline editing for rename-class operations (conversation title, workspace
  name) — no dialog
- Delete confirmation, two tiers: AlertDialog + type-the-name (dangerous) or
  undo toast (recoverable). **New infrastructure required**: there is no
  toast system in the codebase — add one (shadcn/sonner) in the UI
  primitives PR, and note undo-toast implies delayed/cancellable deletion
  semantics in the calling code. These two additions are the sanctioned
  exceptions to the "no new component libraries" non-goal

**State completeness**: every management page gets a layout-matched loading
skeleton, an empty state (icon + guidance + primary action), and an error
state with retry.

## 4. Mobile (chat only) & motion

**Mobile, below `md` (768px)**

- Sidebar becomes a drawer (hamburger + overlay + slide-in)
- Message column 100% width; user bubble max-width 88%
- Input bar fixed at bottom with `env(safe-area-inset-bottom)`; attach /
  preset / thinking collapse into a "+" menu (V1's narrow-screen
  degradation). The "+" menu RE-HOSTS the existing PresetPicker /
  ThinkingControl / attach components (refactored presentation-agnostic
  if needed) — it must not re-implement their logic, or mobile silently
  diverges from desktop on the next preset/thinking feature
- Right panel becomes a full-screen overlay (slide up from bottom)
- Management pages: no redesign — only a non-breaking narrow-viewport
  audit. Real `<Table>` usages already scroll (`ui/table.tsx` wraps every
  table in `overflow-x-auto`); the actual breakage is grid pseudo-tables
  inside `overflow-hidden` wrappers (e.g. admin sandbox CommandRulesTable)
  and two-pane master-detail layouts — fix those with `overflow-x-auto`
  on the wrapper or grid collapse, per layout. A blanket `min-width`
  inside `overflow-hidden` would clip controls unreachably

**Motion applications**

- New message: 8px rise + fade-in. Stagger (30ms/item) applies ONLY to
  short static lists — sidebar conversations, prompt cards, management
  card lists. NOT the message stream: history loads async, auto-scroll
  pins to the bottom (staggered heights would yank the ResizeObserver
  scroll), and CSS can't distinguish first render from streamed appends
- Tool row state change: check scales in; one shared spinner component —
  promote the existing `panel/artifact/PreviewLoading.tsx` (already "the
  shared spinner" for previews) and sweep the ~15 ad-hoc `Loader2`
  call sites onto it
- Panel open/close: see Panel Shell — this is the one sanctioned
  exception to the transform/opacity-only rule: a width transition gated
  to programmatic open/close (transition class applied only then), never
  active during pointer-driven drag-resize (react-resizable-panels drives
  sizes via inline styles per pointermove; a standing transition would
  lag the cursor and reflow the stream every frame)
- Dialogs: scale 0.96→1 + fade

## Implementation strategy

Order chosen to maximize visual impact early while keeping every PR small
and reviewable (no rewrite — restyle in place, per the existing stack:
Tailwind 4 + shadcn/ui + CVA):

1. **Token PR** — `globals.css` variable redefinition + Geist font wiring
   + the theme-default migration (layout.tsx provider flags + the two
   toggle components reading `resolvedTheme`). Fonts come from the
   `geist` npm package (self-hosted via `pnpm add geist`), NOT
   `next/font/google` — the current IBM Plex wiring fetches from Google
   at build time, and CI builds shouldn't gain a network dependency.
   **Honest scope statement**: token-respecting surfaces shift at once,
   but the ~127 hardcoded color classes do NOT — the app runs in a
   deliberate mixed state (new base + old-palette islands like
   AskUserCard/ThinkingBadge) until each area PR lands. This window is
   accepted; chat (the biggest offender cluster) lands first to shorten
   it
2. **UI primitives PR** — restyle the 25 `components/ui/` shadcn
   components against new tokens (radius, borders, states); everything
   downstream inherits. Adds the two new primitives the redesign needs:
   `ui/sheet.tsx` (slide-overs) and a toast system (undo deletions)
3. **Panel Shell PR** — generalize PanelHeader + fold the 4 sibling
   panels under it (own PR: highest regression surface)
4. **Area PRs**: chat (sidebar → message stream → input bar) →
   management modules + pages → mobile → motion polish
5. One PR per area. E2E note: beyond styling-class selectors, the
   placeholder copy change breaks 13 `getByPlaceholder` usages across 7
   spec files (incl. the zh assertion in i18n.spec.ts) — update them in
   the same PR as the copy change
6. Hardcoded colors removed area-by-area. The invariant is then
   ENFORCED, not just swept: the final PR adds an eslint
   `no-restricted-syntax` rule (or CI grep step) rejecting raw palette
   utilities/hex literals in components, with a documented carve-out for
   `chat/widget/` — the widget iframe receives literal hex via srcdoc
   injection (`widgetShell.ts`) because an iframe cannot inherit parent
   CSS variables; its palette values are derived from the token values
   at serialization time instead

**Verification**: each PR posts Playwright screenshots (dark/light × the
pages it touches) for human review — the judgment is the reviewer's, there
is no automated pixel baseline. A hi-fi prototype + user sign-off is
required only for screens where this spec leaves a visual question open
(not for every screen — the direction is already validated).

## Non-goals

- Brand logo design (placeholder mark only)
- Command palette / global keyboard system
- Mobile *redesign* for management pages (the non-breaking
  narrow-viewport audit in §4 IS in scope)
- New animation and icon libraries. Component-library exceptions
  sanctioned in §3: `ui/sheet.tsx` and a toast system, plus the `geist`
  font package
- Backend or API changes (pure frontend initiative)

## References

- Audit + prototypes produced during brainstorming (2026-06-10):
  three-direction style prototype and input-bar variants under
  `.superpowers/brainstorm/` (gitignored, session artifacts)
- Real-app screenshots: chat home, workspace settings, skills, admin
  models, conversation light/dark (session artifacts)
