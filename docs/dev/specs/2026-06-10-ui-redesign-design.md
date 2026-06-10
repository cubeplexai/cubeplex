# UI Redesign — Design Spec

**Date:** 2026-06-10
**Status:** Approved (brainstormed with user, direction validated via hi-fi prototypes)
**Scope:** Full product — chat interface, workspace management pages, admin console

## Problem

The current UI reads as amateur. A frontend audit (all 31 pages, 199 component
files) found:

- 30+ hardcoded ad-hoc colors (`bg-amber-500/10`, `border-blue-200`,
  `bg-green-600`, widget hex literals) bypassing the theme tokens
- No consistent spacing, radius, or type scale (`text-[0.8rem]`, `text-[11px]`,
  `text-[9px]`; `rounded-md/lg/xl/2xl` mixed freely)
- Missing interaction states: weak/absent hover, focus, active feedback;
  inconsistent loading (spinner vs skeleton), missing empty/error states
- Saturated blue user bubbles dominating the chat; assistant avatar noise
- Admin console visually disconnected from the main app (looks like a second
  product)
- 23 right-panel components with at least 6 different header styles
- No mobile support anywhere
- Visual bugs (overlapping avatars in the sidebar footer)

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
| Empty-state home | Keep logo + name; add 3 clickable example-task prompt cards |
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
- Semantic: one token each for success / warning / danger; replaces every
  ad-hoc `amber/green/red/blue` utility in components
- Light mode mirrors the same token structure (white base, `#eaeaea`
  borders, same accent), polished to equal quality

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

Information architecture unchanged: sidebar (248px) / main column /
resizable right panel.

**Sidebar**

- Panel background `#0a0a0a` + 1px right border to separate from `#000` main
- Active conversation: 2px left indicator bar; hover `bg-white/6`
- Group labels (Pinned/Today/…): 11px uppercase + tracking
- Account footer rebuilt: avatar + email + plan badge (fixes the
  overlapping-avatar bug); same component reused in admin top bar

**Message stream**

- Content column max-width 760px, centered
- User message: `#111` bg, `#2a2a2a` border, 6px radius, right-aligned,
  max-width 78%
- Assistant message: no bubble, no avatar — pure typography
- Tool-call group: bordered container of compact mono rows (icon + tool
  name + arg summary + state). Running = spinner, done = green check,
  failed = red cross. Row click opens the right panel (same semantics as
  today)
- Metadata chips (Thinking, Token Usage): 11px mono low-contrast chips on
  one row at message end
- Code blocks: filename header bar + copy button, `#050505` bg

**Input bar (V1)**

- The only "raised" surface on the page: raised bg + strong border; blue
  focus ring on focus-within
- Internal bottom toolbar: attach left; preset + thinking selectors
  right-aligned next to the send button
- Placeholder: "Describe a task…"
- Send button: solid accent; clear disabled state; becomes stop button
  while streaming

**Right panel — unified Panel Shell**

Today: 23 panel components, ≥6 header styles. New structure:

- One shell: header (icon + title + mono subtitle for command/path +
  standard actions: copy / fullscreen / close) + content container with
  shared padding, scroll, empty/loading/error states
- All content types (terminal, search, web fetch, file read/write diff,
  artifact previews, browser live view, skill detail, attachment preview)
  become content adapters inside the shell
- Open/close: 300ms slide with synchronized main-column width transition.
  Switching content updates the header in place — no full-panel flash
- Terminal adapter: real terminal feel — darker bg, mono, ANSI color
  mapping

**Empty-state home**

- Geometric placeholder mark (brand logo is a separate project)
- 3 prompt cards (example tasks: analyze a data file / research a topic /
  automate a workflow); click fills the input

**State completeness**

- Streaming: blinking end-of-line cursor; spinner on running tools
- Loading: skeletons shaped like the real layout (list + message history)
- Errors: inline error bar (icon + reason + retry) replacing RunErrorBubble
- Empty: standard empty-state component (icon + copy + primary action)

## 3. Management pages (workspace + admin)

One **management page template** applied to all 26 pages:

- **Page header**: title (20px/600) + one-line description (13px secondary)
  + at most one solid accent button, right-aligned
- **Toolbar row**: search input + segmented filter control, one style
  everywhere
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
  wizard stays a full-page wizard
- Inline editing for rename-class operations (conversation title, workspace
  name) — no dialog
- Delete confirmation, two tiers: AlertDialog + type-the-name (dangerous) or
  undo toast (recoverable)

**State completeness**: every management page gets a layout-matched loading
skeleton, an empty state (icon + guidance + primary action), and an error
state with retry.

## 4. Mobile (chat only) & motion

**Mobile, below `md` (768px)**

- Sidebar becomes a drawer (hamburger + overlay + slide-in)
- Message column 100% width; user bubble max-width 88%
- Input bar fixed at bottom with `env(safe-area-inset-bottom)`; attach /
  preset / thinking collapse into a "+" menu (V1's narrow-screen
  degradation)
- Right panel becomes a full-screen overlay (slide up from bottom)
- Management pages: no redesign; `min-width` + horizontal scroll fallback
  so nothing breaks

**Motion applications**

- New message: 8px rise + fade-in; first list render: 30ms/item stagger
- Tool row state change: check scales in; one shared spinner component
- Panel: slide + width transition; dialogs: scale 0.96→1 + fade

## Implementation strategy

Order chosen to maximize visual impact early while keeping every PR small
and reviewable (no rewrite — restyle in place, per the existing stack:
Tailwind 4 + shadcn/ui + CVA):

1. **Token PR** — `globals.css` variable redefinition + Geist font wiring.
   Zero component changes; whole app shifts at once. Lowest risk, biggest
   single impact
2. **UI primitives PR** — restyle the 25 `components/ui/` shadcn components
   against new tokens (radius, borders, states); everything downstream
   inherits
3. **Area PRs**: chat (sidebar → message stream → input bar → panel shell)
   → management template + pages → mobile → motion polish
4. One PR per area; E2E suite green before merge (existing Playwright
   assertions are behavior-level; fix selectors that depend on styling
   classes)
5. Hardcoded colors removed area-by-area; final PR is a global grep sweep
   asserting no raw color values remain in components

**Verification**: per-PR Playwright screenshots (dark/light × key pages)
compared against the approved direction; major screens get a hi-fi
prototype for user sign-off before implementation.

## Non-goals

- Brand logo design (placeholder mark only)
- Command palette / global keyboard system
- Mobile for management pages
- New animation/icon/component libraries
- Backend or API changes (pure frontend initiative)

## References

- Audit + prototypes produced during brainstorming (2026-06-10):
  three-direction style prototype and input-bar variants under
  `.superpowers/brainstorm/` (gitignored, session artifacts)
- Real-app screenshots: chat home, workspace settings, skills, admin
  models, conversation light/dark (session artifacts)
