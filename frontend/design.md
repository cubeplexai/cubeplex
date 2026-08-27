# CubePlex default theme

Living summary of the **default** product theme (the Vercel-Mono family).
Source of truth for values is
[`packages/web/app/globals.css`](packages/web/app/globals.css). If this
doc and that file disagree, believe the CSS.

The app still has a second family, **Operator** (`.operator-light` /
`.operator-dark`). It is registered with `next-themes` and its tokens stay
in `globals.css`, but it is not user-selectable. The product writes only
`light` / `dark` / `system` of the default family.

## Character

Restrained grayscale surfaces, crisp 1px borders for layering, one blue
accent, small radii, Geist for UI and Geist Mono for data. Calibrated
against Vercel Geist and Linear — not pure black + pure white.

- **Light** is the `@theme` baseline.
- **Dark** is a class override (`.dark` on `<html>`).
- New users follow the OS via `defaultTheme="system"`.
- Choosing light or dark in the UI leaves system mode (no third "system"
  option).

Accent (`#0070f3`) is for primary actions, focus rings, and active
indicators. Status color (success / warning / danger / info) is not
accent. Everything else stays near-neutral.

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
  snapshot computed tokens (see `WidgetView`)

## Color

Tailwind 4 maps `--color-*` to utilities (`bg-background`, `text-faint`,
`border-border-strong`, `bg-success-surface`, …). Use those classes. Do
not hardcode hex or `gray-*` / `blue-*` / `amber-*` in product UI.

### Surfaces (elevation)

Layering is mostly **border + background step**, not shadow. Four
surfaces:

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

Dark contrast is tuned down from the original spec (`#000` / `#ededed`).
Body text sits around 12:1 on the soft black, not 17:1.

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
`destructive` aliases danger for shadcn.

**Light**

|         | surface   | border    | fg        | solid     |
| ------- | --------- | --------- | --------- | --------- |
| success | `#e6f6ee` | `#b3e6cc` | `#0f7b3f` | `#17a35a` |
| warning | `#fff7e6` | `#ffe1a6` | `#925f00` | `#f5a623` |
| danger  | `#fdebeb` | `#f5c2c2` | `#c22929` | `#e5484d` |
| info    | `#e9f2fe` | `#c0dcfb` | `#0a5cc2` | `#3b82f6` |

**Dark** — lower saturation, dimmer foregrounds (the earlier bright mint /
hot red poked on a dark canvas):

|         | surface                   | border                    | fg        | solid     |
| ------- | ------------------------- | ------------------------- | --------- | --------- |
| success | `rgba(45, 145, 90, 0.1)`  | `rgba(45, 145, 90, 0.3)`  | `#6db58a` | `#3f8c63` |
| warning | `rgba(200, 145, 50, 0.1)` | `rgba(200, 145, 50, 0.3)` | `#d1a164` | `#b07e3a` |
| danger  | `rgba(195, 75, 80, 0.1)`  | `rgba(195, 75, 80, 0.3)`  | `#d97478` | `#b54146` |
| info    | `rgba(80, 130, 200, 0.1)` | `rgba(80, 130, 200, 0.3)` | `#7eaadf` | `#4a78b8` |

`destructive` = `#e5484d` on `#ffffff` in both modes.

Info covers awaiting-input, notices, and “official” badges — blues that
are **not** actions. Keep `#0070f3` for actions.

## Typography

| Role                        | Font                                                                               |
| --------------------------- | ---------------------------------------------------------------------------------- |
| UI + body                   | Geist Sans → `-apple-system`, `PingFang SC`, `Microsoft YaHei`, `Noto Sans CJK SC` |
| Code, data, commands, paths | Geist Mono → `ui-monospace`, `SF Mono`                                             |

Loaded in `app/layout.tsx` as `--font-geist-sans` / `--font-geist-mono`.
(Inter Tight and JetBrains Mono are loaded for the Operator family only.)

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

Do not add `text-[11px]` / `text-[13px]` one-offs. Section labels: 11px
uppercase + wider tracking (`text-2xs font-medium uppercase tracking-wider text-faint`).
Numbers in data (tokens, cost, timestamps): `tabular-nums`.

## Shape

| Token       | Value | Typical use                        |
| ----------- | ----- | ---------------------------------- |
| `radius-xs` | 4px   | Badges, chips                      |
| `radius`    | 6px   | Buttons, inputs, cards (`rounded`) |
| `radius-lg` | 10px  | Panels, modals (`rounded-lg`)      |

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
duration site-wide.

Press feedback on buttons: `active:translate-y-px active:scale-[0.98]`.

## Syntax highlighting

Code blocks use the GitHub palette (`.hljs-*` in `globals.css`), not the
status tokens. Container chrome is `bg-muted` / sunken; only the spans
are colored. Light and `.dark` each have their own set.

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
  Lucide icons).
- Hover: `hover:bg-accent` or `hover:bg-muted`, 120ms.
- Focus: `focus-visible:ring-2 focus-visible:ring-ring` (or the button’s
  `ring-3 ring-ring/50`).
- Selected list rows: raised fill + a 2px primary indicator, not a
  saturated background.
- Chat column max-width is 760px; user bubbles are `bg-raised`, 6px
  radius, right-aligned, max 78% on desktop.

## Operator family (kept, not shipped)

Do not delete `.operator-light` / `.operator-dark` or drop those names
from `ThemeProvider`. They are an alternate voice: Inter Tight +
JetBrains Mono, inverse elevation, high-contrast **neutral** CTA (ink,
not brand blue), indigo reserved for the focus ring, tighter radii
(3 / 5 / 6), no shadows, `.uppercase` auto-promoted to mono.

To re-expose it as a user flavor, restore a family toggle that composes
`{operator-}{light|dark}` and remove `DefaultThemeGuard`.
