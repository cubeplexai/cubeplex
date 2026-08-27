---
name: cubeplex-frontend-design
description: >
  Visual language and token contract for the CubePlex product UI
  (frontend/packages/web). Load when shaping, building, restyling, or
  reviewing user-facing pages and components: theme, color, type,
  spacing, motion, chat, list-detail, admin vs workspace, empty /
  loading / error states, copy that sits in the UI. Skip backend-only
  work, telemetry, generated files, and docs with no shipped UI.
---

# CubePlex product UI

This is the design contract for the **shipped product**, not a restyle
brief. Keep CubePlex looking like CubePlex. The default family (Vercel-Mono
direction: restrained grayscale, one blue accent, Geist) is already locked
in `globals.css`; this file tells agents how to use it.

Source of truth for values is
[`packages/web/app/globals.css`](packages/web/app/globals.css). If this
doc and that file disagree, believe the CSS. Frozen history lives in
[`docs/dev/specs/2026-06-10-ui-redesign-design.md`](../docs/dev/specs/2026-06-10-ui-redesign-design.md)
— do not back-port tuned tokens into the spec, and do not treat the spec's
prototype numbers (e.g. a 760px chat column) as current.

[`docs/STYLE_GUIDE.md`](docs/STYLE_GUIDE.md) is a previous product. Ignore
its palettes, fonts, and dark-first rules.

## When to load

Read this file before changing anything a user sees or interacts with:
pages, layouts, components, tokens, theme wiring, empty/error/loading
states, and UI copy.

Skip it for backend-only work, API contracts with no visual effect,
telemetry, generated files, and documentation that is not a shipped
screen.

## Priority when requirements compete

1. Preserve product behavior, i18n keys, workspace-vs-admin scope
   isolation, and existing component APIs.
2. Use named tokens and existing primitives (`@/components/ui`,
   `@/components/shared`). Do not invent a parallel look.
3. Make the user's job and the primary action unmistakable.
4. Match the **default** family (light / dark / system). Do not revive
   Operator as a user-facing flavor.
5. Cover every reachable state the change can enter.
6. Refine density, motion, and alignment last, without weakening
   hierarchy.

Do not redesign the surrounding page to "match a template." Change the
smallest coherent surface that solves the job.

## Operating contract

- **Start with the job, not the pixels.** Who is acting, on what object,
  to accomplish what, and what the system will change.
- **Reuse before restyle.** `Button`, `EmptyState`, `SectionHeader`,
  `ListDetailLayout`, `RailCard`, and the panel shell already encode
  decisions. Forking them recreates the divergence they were written to
  stop.
- **CSS is the token source.** Hex in this file is an index. New color
  goes into `globals.css` as a token, then into a utility class — never
  as a one-off in a component.
- **Shipped code is evidence, not automatic precedent.** A nearby
  `text-[11px]` or `bg-amber-500/10` is a leftover to avoid, not a
  pattern to copy. The ESLint raw-palette guard in
  `packages/web/eslint.config.mjs` is the mechanical check; this file is
  the judgment.
- **Decide before decorating.** Information architecture, component
  choice, and states come before new color, type, or motion.
- **Verify the rendered surface.** Source inspection is not visual
  proof. Exercise the change the way a user would, including the other
  screens that share the state you touched.

## Character

Restrained grayscale surfaces, crisp 1px borders for layering, one blue
accent, small radii, Geist for UI and Geist Mono for data. Tuned for
long sessions — not pure black + pure white, not a marketing page.

- **Light** is the `@theme` baseline.
- **Dark** is a class override (`.dark` on `<html>`).
- New users follow the OS via `defaultTheme="system"`.
- Choosing light or dark in the UI leaves system mode. There is no third
  "system" control.

Accent (`#0070f3`) is for primary actions, focus rings, and active
indicators. Status color (success / warning / danger / info) is not
accent. Everything else stays near-neutral.

The app still has a second family, **Operator** (`.operator-light` /
`.operator-dark`). It is registered with `next-themes` and its tokens
stay in `globals.css`, but it is not user-selectable. The product writes
only `light` / `dark` / `system` of the default family. See the Operator
section at the bottom — do not delete it, and do not build new UI on it.

## Architecture

Two orthogonal axes, even though only one family ships in the UI:

| Axis   | Values                                          | `<html>` class                                           |
| ------ | ----------------------------------------------- | -------------------------------------------------------- |
| Family | default (ships) / operator (registered, hidden) | _(none)_ / `operator-*`                                  |
| Mode   | light / dark / system                           | `light` / `dark` (or `operator-light` / `operator-dark`) |

`@custom-variant dark` matches both `.dark` and `.operator-dark`, so
`dark:` utilities work for either family.

Wiring:

- `next-themes` `ThemeProvider` in `packages/web/app/layout.tsx`
- Light/dark toggle: header `ThemeToggle` and the account menu
- `DefaultThemeGuard` remaps a leftover `operator-*` localStorage value
  onto `light` / `dark` so users are not stuck after the flavor picker
  was removed
- Widgets cannot inherit CSS variables into an iframe `srcdoc`; they
  snapshot computed tokens (see `WidgetView`). Literal hex there is
  structural, not a license to hardcode color in the rest of the app.

Stack to preserve: Next.js App Router, React 19, Tailwind 4, shadcn
`base-nova` in `packages/web/components/ui`, Lucide, next-intl. Add
components with `pnpm dlx shadcn@latest add …` from `packages/web/`.
Do not introduce a second component library, icon kit, or animation
library.

## Color

Tailwind 4 maps `--color-*` to utilities (`bg-background`, `text-faint`,
`border-border-strong`, `bg-success-surface`, …). Use those classes. Do
not hardcode hex or `gray-*` / `blue-*` / `amber-*` in product UI.

### Surfaces

Layering is **border + background step**, not shadow. Named surface
tokens (several share a hex on purpose — pick by role, not by looking
for a unique swatch):

| Token        | Light     | Dark      | Typical use                                       |
| ------------ | --------- | --------- | ------------------------------------------------- |
| `background` | `#ffffff` | `#0a0a0a` | Page canvas                                       |
| `card`       | `#fafafa` | `#141414` | Panels, sidebar                                   |
| `raised`     | `#f5f5f5` | `#181818` | Input bar, user bubble, hover fills               |
| `sunken`     | `#fafafa` | `#050505` | Code blocks, terminal                             |
| `popover`    | `#ffffff` | `#141414` | Menus, dropdowns                                  |
| `accent`     | `#f0f0f0` | `#1c1c1c` | Hover slot (selected rows use raised + indicator) |
| `muted`      | `#f5f5f5` | `#181818` | Quiet fills                                       |
| `secondary`  | `#f5f5f5` | `#181818` | Secondary button fill                             |

Dark contrast is intentionally below a pure-black / near-white pairing.
Body text sits around 12:1 on the soft black.

### Text

| Token              | Light     | Dark      | Typical use                               |
| ------------------ | --------- | --------- | ----------------------------------------- |
| `foreground`       | `#171717` | `#d4d4d7` | Body                                      |
| `muted-foreground` | `#666666` | `#8e8e93` | Secondary                                 |
| `faint`            | `#999999` | `#595960` | Labels, hints, captions, section eyebrows |

### Chrome

| Token           | Light     | Dark      | Typical use                               |
| --------------- | --------- | --------- | ----------------------------------------- |
| `border`        | `#eaeaea` | `#262626` | Default 1px edge                          |
| `border-strong` | `#d4d4d4` | `#3a3a3a` | Focused input, toast, stronger separators |
| `input`         | `#eaeaea` | `#262626` | Input border                              |
| `ring`          | `#0070f3` | `#0070f3` | Focus ring (same as primary)              |

### Accent and brand

| Token                  | Value     | Typical use                                            |
| ---------------------- | --------- | ------------------------------------------------------ |
| `primary`              | `#0070f3` | Solid CTA, active indicator, links that are actions    |
| `primary-foreground`   | `#ffffff` | Text on primary                                        |
| `--brand-mark-primary` | `#1268e8` | Logo mark stroke only (`CubePlexLogo`). Not a UI fill. |

Primary is the **same hex in light and dark**. Do not invent a second
brand blue for dark mode.

### Status

Each status is a four-slot set: `surface` / `border` / `fg` / `solid`.
`destructive` is the shadcn slot aliased onto danger (`#e5484d` fill,
`#ffffff` text on the solid slot). The default `Button variant="destructive"`
is a tinted surface (`bg-destructive/10 text-destructive`), not a solid
red fill. Use the solid slot only when the action is the page's primary
destructive control.

**Light**

|         | surface   | border    | fg        | solid     |
| ------- | --------- | --------- | --------- | --------- |
| success | `#e6f6ee` | `#b3e6cc` | `#0f7b3f` | `#17a35a` |
| warning | `#fff7e6` | `#ffe1a6` | `#925f00` | `#f5a623` |
| danger  | `#fdebeb` | `#f5c2c2` | `#c22929` | `#e5484d` |
| info    | `#e9f2fe` | `#c0dcfb` | `#0a5cc2` | `#3b82f6` |

**Dark** — lower saturation, dimmer foregrounds so status does not poke
on a dark canvas:

|         | surface                   | border                    | fg        | solid     |
| ------- | ------------------------- | ------------------------- | --------- | --------- |
| success | `rgba(45, 145, 90, 0.1)`  | `rgba(45, 145, 90, 0.3)`  | `#6db58a` | `#3f8c63` |
| warning | `rgba(200, 145, 50, 0.1)` | `rgba(200, 145, 50, 0.3)` | `#d1a164` | `#b07e3a` |
| danger  | `rgba(195, 75, 80, 0.1)`  | `rgba(195, 75, 80, 0.3)`  | `#d97478` | `#b54146` |
| info    | `rgba(80, 130, 200, 0.1)` | `rgba(80, 130, 200, 0.3)` | `#7eaadf` | `#4a78b8` |

Info covers awaiting-input, notices, and "official" badges — blues that
are **not** actions. Keep `#0070f3` for actions.

## Typography

| Role                        | Font                                                                               |
| --------------------------- | ---------------------------------------------------------------------------------- |
| UI + body                   | Geist Sans → `-apple-system`, `PingFang SC`, `Microsoft YaHei`, `Noto Sans CJK SC` |
| Code, data, commands, paths | Geist Mono → `ui-monospace`, `SF Mono`                                             |

Loaded in `app/layout.tsx` as `--font-geist-sans` / `--font-geist-mono`.
Inter Tight and JetBrains Mono are loaded for the Operator family only —
do not use them on default-family UI.

### Size scale

| Step | Size | Line height        | Utility                                             |
| ---- | ---- | ------------------ | --------------------------------------------------- |
| 2xs  | 11px | 1.45               | `text-2xs` — eyebrows, chips, meta                  |
| xs   | 12px | (Tailwind default) | `text-xs`                                           |
| sm   | 13px | 1.55               | `text-sm`                                           |
| md   | 14px | 1.55               | `text-md` — body in chat / inputs                   |
| base | 16px | (Tailwind default) | `text-base` — leave on inputs (iOS zoom) and titles |
| xl   | 20px | 1.35               | `text-xl`                                           |
| 2xl  | 24px | 1.25               | `text-2xl`                                          |

Do not add `text-[11px]` / `text-[13px]` one-offs. Prefer `text-2xs`
over a leftover `text-[11px]`. Section labels: `text-2xs font-medium
uppercase tracking-wider text-faint`. Numbers in data (tokens, cost,
timestamps): `tabular-nums`.

`text-lg` appears on a few pane titles (`SectionHeader`). Do not spread
it. New titles: `text-base` / `text-xl` from the table, or reuse
`SectionHeader`.

## Shape

| Token       | Value | Typical use                        |
| ----------- | ----- | ---------------------------------- |
| `radius-xs` | 4px   | Badges, chips                      |
| `radius`    | 6px   | Buttons, inputs, cards (`rounded`) |
| `radius-lg` | 10px  | Panels, modals (`rounded-lg`)      |

`EmptyState` and `RailCard` use `rounded-xl`. Reuse that on those
surfaces; do not invent a fourth radius for a one-off.

Spacing is a 4px grid (Tailwind defaults). Prefer `p-2` / `p-4` / `gap-2`
/ `gap-4` over arbitrary values.

Shadows are the exception, not the layering system: `shadow-sm` on the
composer, `shadow-lg` on toasts and the account menu. Do not pile
elevation shadows on panels — use `border` vs `border-strong`.

## Motion

| Token            | Value                           | Typical use                          |
| ---------------- | ------------------------------- | ------------------------------------ |
| `duration-fast`  | 120ms                           | Hover / press (`duration-fast`)      |
| `duration-base`  | 200ms                           | Expand / fade (`duration-base`)      |
| `duration-slow`  | 300ms                           | Panel open / slide (`duration-slow`) |
| `ease-out-quart` | `cubic-bezier(0.16, 1, 0.3, 1)` | Default easing                       |

`--duration-*` powers Tailwind `duration-*` utilities.
`--transition-duration-*` is the same set for handwritten `transition:`
rules (panel width, `animate-rise-in`).

Named animations in `globals.css`: `animate-rise-in`, `animate-scale-in`,
thinking sparkle/shimmer, auth-cube drift. Prefer `transform` + `opacity`.
`prefers-reduced-motion: reduce` collapses animation and transition
duration site-wide. Do not add auto-playing decorative motion, marquees,
or scroll-triggered reveals.

Press feedback on buttons: `active:translate-y-px active:scale-[0.98]`.

## Syntax highlighting

Code blocks use the GitHub palette (`.hljs-*` in `globals.css`), not the
status tokens. Container chrome is `bg-muted` / sunken; only the spans
are colored. Light and `.dark` each have their own set.

## Product surfaces

These are the layouts that already exist. Fit new UI into them; do not
invent a fourth chrome.

### Chat

Sidebar / main column / resizable right panel. Column width is
`CHAT_COLUMN_CLASS` in `packages/web/lib/chatLayout.ts`
(`max-w-[57.6rem]`, centered) — keep the composer and the message list
on the same class so the edges line up.

- User bubbles: `bg-raised`, `border-border`, `rounded-lg rounded-br-xs`,
  right-aligned, `max-w-[88%] md:max-w-[78%]`.
- Assistant replies: no bubble, no avatar — typography in
  `ASSISTANT_CONTENT_MAX_CLASS`.
- Selected conversations in the sidebar: raised fill + a 2px primary
  left indicator, not a saturated background.
- Tool-call groups are bordered compact mono rows; row click opens the
  right panel. Do not restyle them into colorful cards.

### Workspace list-detail

Triggers, scheduled tasks, IM, skills, and similar pages use
`ListDetailLayout` + `RailCard` + `SectionHeader` + `EmptyState` under
`components/shared/`.

- Desktop (≥768px): fixed list rail + flex detail. The rail width does
  not change when a row is selected.
- Mobile: list fills the pane; the detail is a full-screen overlay with
  a back control.
- Selected `RailCard`: `border-primary/40 bg-primary/5` plus a 2px
  primary left bar. Do not invent a third selected style.
- Empty states: the shared `EmptyState` (dashed border, icon, title,
  hint, optional CTA). Pair any `max-w-*` wrapper with `w-full` so the
  card does not shrink-wrap and the header action does not drift.
- One primary action per `SectionHeader`, pinned to the content width
  (`PANE_CONTENT_WIDTH` / `SETTINGS_CONTENT_WIDTH`).

Workspace and org-admin stay **separate pages** even when the modules
match. Reuse `<List>`, `<DetailPanel>`, … — never
`mode?: 'admin' | 'workspace'` on a page component.

### Panels

Right-panel content goes through the shared panel shell (`PanelHeader` +
adapters). Switching content updates the header in place. Terminal /
code adapters stay sunken + mono. Do not hand-roll a second header.

### Auth and marketing-adjacent screens

Login / setup may be quieter than the app chrome. They still use the
same tokens, Geist, and primary accent. Do not introduce a campaign
look (centered hero, badge row, gradient mesh, three feature cards).

## States

Design every state the product can actually enter. Minimum set for a
changed surface:

- loading (prefer the control's own busy affordance; keep the label)
- empty (shared `EmptyState`, with a next step when one exists)
- populated
- validation / recoverable error (do not wipe the user's input)
- permission / disabled
- destructive (verb + object; undo when the system can honestly support
  it — the toast + delayed-delete path already exists)
- compact vs wide (chat is the mobile-supported surface; management
  pages must not overflow or trap the detail off-screen)

Do not add a state the product cannot reach just to look complete.

## Copy

User-visible strings go through next-intl (`en` and `zh` together;
`scripts/check-i18n-keys.mjs` enforces key parity). Write the sentence
the least specialized user of that screen can act on. Name the object
and the consequence on destructive actions. Do not ship `Confirm`, `OK`,
or a bare verb as the primary destructive CTA.

Placeholder tone: the user commands the agent ("Describe a task…"), not
the other way around.

## Accessibility

shadcn/Radix covers a baseline. Still required on every change:

- Icon-only buttons have `aria-label`.
- Decorative icons are `aria-hidden`.
- Focus is visible (`focus-visible:ring-2 focus-visible:ring-ring` or
  the button's `ring-3 ring-ring/50`). Never `outline-none` without a
  replacement.
- Color is not the only state cue.
- Hit targets stay usable on touch; input text stays `text-base` where
  iOS zoom matters.

Deeper a11y review: the `web-design-guidelines` skill.

## Reject

Do not ship these on product UI. They are the usual generated defaults,
not CubePlex:

- Raw hex, `rgb()`, or Tailwind palette utilities (`bg-gray-900`,
  `text-blue-500`, `border-amber-200`, …) outside the widget iframe
  exception.
- A second brand blue, or status color used as the accent.
- Decorative gradients, gradient text, glass, blobs, glows, mesh
  backgrounds, ornamental shadows.
- Generic centered hero + card grid, badge/pill rows for ordinary
  metadata, nested cards to fake hierarchy.
- New radii, type sizes, or durations invented per component.
- Shadows as the elevation system on panels.
- Auto-playing decorative motion, simulated typing, pulsing status
  ornaments.
- Operator fonts, Operator tokens, or a family picker in new UI.
- A `mode?: 'admin' | 'workspace'` page, or one route that restyles
  itself for both scopes.
- Hand-rolled empty states, pane headers, or list-detail chrome when
  the shared components already cover it.
- Copying [`docs/STYLE_GUIDE.md`](docs/STYLE_GUIDE.md) (stale Inter /
  HSL / dark-first palette).

Restraint here means hierarchy, alignment, and one accent — not empty
margins and thin rules for their own sake. If a screen feels unfinished,
fix the job, the states, or the reuse; do not fill the gap with chrome.

## Verify

Before calling UI work done:

1. The primary job and primary action are obvious on the changed screen.
2. Light and dark both hold contrast and hierarchy.
3. Every reachable state you touched still works, including empty and
   error.
4. Other routes that share the component or store still behave.
5. Compact and wide layouts do not overflow or hide the main action.
6. You exercised it in the browser (or the closest substitute) rather
   than trusting a static screenshot.

## Usage

```tsx
<div className="bg-background text-foreground">
<div className="bg-card border border-border">
<button className="bg-primary text-primary-foreground">
<p className="text-sm text-muted-foreground">
<span className="text-2xs text-faint uppercase tracking-wider">
<div className="bg-raised border border-border-strong">
<pre className="bg-sunken">
<div className="bg-success-surface text-success-fg border border-success-border">
```

- Import UI primitives from `@/components/ui/` (shadcn `base-nova`,
  Lucide). Shared chrome from `@/components/shared/`.
- Hover: `hover:bg-accent` or `hover:bg-muted`, 120ms.
- Focus: `focus-visible:ring-2 focus-visible:ring-ring` (or the button's
  `ring-3 ring-ring/50`).
- Chat column: `CHAT_COLUMN_CLASS`, not a one-off `max-w-*`.

## Operator family (kept, not shipped)

Do not delete `.operator-light` / `.operator-dark` or drop those names
from `ThemeProvider`. They are an alternate voice: Inter Tight +
JetBrains Mono, inverse elevation, high-contrast **neutral** CTA (ink,
not brand blue), indigo reserved for the focus ring, tighter radii
(3 / 5 / 6), no shadows, `.uppercase` auto-promoted to mono.

To re-expose it as a user flavor, restore a family toggle that composes
`{operator-}{light|dark}` and remove `DefaultThemeGuard`. Until then,
new work stays on `light` / `dark` / `system`.
