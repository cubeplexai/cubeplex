# List-Detail (左右分栏) standard architecture

Status: design · 2026-06-16 · area: frontend/packages/web

## Why

Several workspace pages are "list on the left, detail on the right": triggers,
scheduled tasks, IM accounts, skills. They were each built independently, so the
same interaction looks and behaves differently page to page. A round of UI
polish unified the page headers and empty states, but the list-detail pages
still diverge in four concrete ways (verified by reading the code and by
screenshots of the triggers and scheduled-tasks pages):

1. **List paradigm differs.** Triggers renders a bordered `<Table>` with column
   headers (Name / Status / Actions); scheduled tasks renders rich cards
   (title + status badge + schedule + next-fire + prompt preview + `...` menu).
   The selected-row visual language differs too: triggers uses a faint gray
   background, scheduled uses a primary-colored border + left accent bar.
2. **Empty state width is inconsistent.** Both use the shared `EmptyState`, but
   the trigger empty card is visibly narrower. Root cause: the content-width
   wrapper uses `max-w-4xl` **without `w-full`**, so the box shrink-wraps to its
   text. The same bug makes the header's "create" button drift horizontally
   between the empty and detail states — this is why "button position feels
   random" was never fully fixed.
3. **Detail information architecture is not equivalent.** The trigger detail is
   a full detail page (close affordance, title + status + delete, counter
   cards, settings card, events table). The scheduled-task detail is only a run
   history list — it doesn't even repeat the task's name, schedule, or actions
   (pause/edit/delete live only in the card's `...` menu).
4. **Empty states inside the detail are ad-hoc.** "No runs yet" and "No events
   recorded yet" are two different hand-rolled dashed boxes (one hardcoded in
   English), neither using the shared `EmptyState`.

On top of that, the layout has **no mobile story at all**: the fixed 360px list
rail plus a flex detail overflows the viewport on a 390px screen — the detail
panel is pushed off the right edge.

## Decisions (locked)

- **List items are cards**, not tables. A narrow rail (~360px) is too cramped
  for multi-column tables, and the trigger table looks empty with few rows.
  Triggers moves to cards; scheduled tasks already uses cards. One shared card
  style and one shared selected-state.
- **Mobile = full-screen overlay with a back button.** Below 768px the layout
  collapses to a single column: the list fills the screen; tapping an item
  opens the detail as a full-screen overlay with a back button; back returns to
  the list. This matches the existing AppShell sidebar drawer convention. (Not
  a separate route — an in-place overlay.)
- Reuse existing infra: `useMediaQuery('(min-width: 768px)')` for the breakpoint
  (same as AppShell), the shared `EmptyState`, and the `SectionHeader` already
  introduced in this branch.

## Target architecture

Five shared pieces under `components/shared/`, consumed by every list-detail
page (triggers, scheduled tasks, and later IM / skills):

### 1. `<ListDetailLayout>` (responsive; upgrades `ListDetailPane`)
- **Desktop (≥768px):** fixed-width list rail + flex detail. When nothing is
  selected the detail area shows a shared placeholder (icon + hint). The list
  width never changes when a row is selected (already fixed in this branch).
- **Mobile (<768px):** single column. The list fills the pane. When an item is
  selected the detail renders as a full-screen overlay (`fixed inset-0`) with a
  back button in its header; dismissing clears the selection. Loading / empty
  (no data at all) render full-width in both modes.
- Props: `list`, `detail`, `placeholder`, plus a way to know selection so mobile
  knows whether to show the overlay. The page owns selection state and passes it
  down (keeps the layout dumb).

### 2. `<RailCard>` — the one list-item style
A single card used by every rail list: title, optional status badge, a secondary
line, optional meta line, an optional trailing actions slot (`...` menu), and a
unified selected state (left accent bar + subtle primary tint) and hover. Both
triggers and scheduled tasks render their rows through this so selection, hover,
padding, and the click target are identical. Tables remain possible for future
wide-list pages, but the rail default is `RailCard`.

### 3. Empty state + placeholder
- Keep one `EmptyState` (icon + title + hint + optional CTA). **Fix its width**:
  pair `max-w-*` with `w-full` wherever it is wrapped so it never shrink-wraps.
- One "nothing selected" placeholder (icon + hint), provided by
  `ListDetailLayout`.
- A small `EmptyState` variant for in-detail empties (no runs / no events) —
  delete the bespoke dashed boxes and the hardcoded English.

### 4. `<DetailPanel>` — the one detail shell
Standard header: back/close (back shown on mobile), title + status badge, and a
right-aligned primary-actions slot; standard body padding and width. The trigger
detail adopts it. The **scheduled-task detail gains a real header** (task name +
schedule summary + pause/edit/delete) with run history as one section inside it —
so the two details have matching chrome and information depth.

### 5. `SectionHeader` width fix
- Inner row becomes `w-full max-w-4xl` so the action button sits at a stable
  position instead of drifting.
- Add a width mode: list-detail pages use a **full-width** header (button aligns
  with the detail panel's right edge); single-column settings pages use the
  **contained** width. This removes the button drift on the list-detail pages.

## Per-page outcome

- **Triggers:** table → `RailCard` list; detail wrapped in `DetailPanel`;
  full-screen detail on mobile.
- **Scheduled tasks:** cards adopt `RailCard`; detail wrapped in `DetailPanel`
  with a real task header + actions, run history as a section; mobile overlay.
- **IM / skills:** same layout later; skills is already a 360px rail and is close
  to a drop-in.

## Rollout (each step independently verifiable)

1. **Primitives:** responsive `ListDetailLayout`, `RailCard`, `DetailPanel`,
   `EmptyState` width fix, `SectionHeader` width mode.
2. **Triggers** adopt the primitives (cards, detail shell, mobile).
3. **Scheduled tasks** adopt the primitives (card rule, detail header + actions,
   mobile).
4. **Detail empties / hardcoded English** cleanup + i18n parity.
5. **Verify:** desktop + mobile screenshots for both pages; type-check, lint,
   i18n parity.

## Out of scope

- Migrating IM / skills (follow-up; the primitives are designed to absorb them).
- The orphaned `/triggers/[id]`, `/memory`, `/sandbox-env` route files (separate
  cleanup).
