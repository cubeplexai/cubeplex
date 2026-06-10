# UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full-product "Vercel Mono" UI redesign per
`docs/dev/specs/2026-06-10-ui-redesign-design.md` on the single
`feat/ui-redesign` branch, in 7 stages, each ending with a local
`/code-review` round; one big PR at the end.

**Architecture:** Token-first: redefine the existing shadcn semantic CSS
variables in `globals.css` (so all 25 `ui/` primitives inherit without
code changes), add new surface/status/motion tokens, then restyle outward
— primitives → panel shell → chat → management → mobile → motion. Restyle
in place; the only structural refactor is the Panel Shell generalization.

**Tech Stack:** Next.js 16 / React 19 / Tailwind 4 (`@theme` in
globals.css, no config file) / shadcn-style components with CVA /
next-themes / next-intl / Playwright E2E / `geist` font package (new) /
sonner toast (new) / shadcn Sheet (new).

**Execution rules (apply to every stage):**

- Worktree: `/home/chris/cubebox/.worktrees/feat/ui-redesign` — run
  `cat .worktree.env` first. Frontend dev: `pnpm dev` from `frontend/`
  (port 3001). Backend: port 8001.
- All frontend commands run from `frontend/` with pnpm. `@cubebox/core`
  must build before web sees type changes.
- **Exact command forms** (script names verified against package.json —
  do not improvise):
  - Typecheck: `pnpm -r type-check` (NOT `typecheck` — that silently
    no-ops with exit 0)
  - Lint: `pnpm -r lint` · Unit tests: `pnpm --filter web test`
  - E2E: ALWAYS from `frontend/` so `playwright.config.ts` (worktree
    ports, baseURL, webServer) loads:
    `pnpm exec playwright test packages/web/__tests__/e2e/<spec>` —
    NEVER `pnpm --filter web exec playwright test …` (cwd=packages/web
    loses the config and tests hit port 3000 = wrong server)
- After each stage: incremental tests (only suites the stage touched;
  full sweep happens in Stage 7) → screenshots (Stage 0 harness) →
  `/code-review` → fix findings → commit. Review effort per stage
  (single source of truth): Stage 0/1/2/6 = medium, Stage 3/4/5 = high,
  Stage 7 = max.
- Commit per coherent chunk (a task or a feature-area batch — see Stage
  5). Each commit pays ~30s of whole-workspace lint hooks; don't slice
  one-page diffs into per-page commits. Message style:
  `feat(ui): <stage>: <what>`.
- i18n: every new/changed user-facing string gets keys in BOTH
  `messages/en.json` and `messages/zh.json` (pre-commit enforces parity).
- No raw color values in components — token utilities only. Exception:
  `components/chat/widget/` (iframe srcdoc, see Stage 7).

---

## Stage 0: Prep & screenshot harness

### Task 0.1: Verify environment

**Files:** none

- [ ] **Step 1:** `cat .worktree.env` — confirm ports 8001/3001, DB
      `cubebox_feat_ui_redesign`.
- [ ] **Step 2:** Start both servers if not running:

```bash
cd /home/chris/cubebox/.worktrees/feat/ui-redesign
set -a && source .worktree.env && set +a
(cd backend && nohup uv run python main.py > /tmp/cubebox-ui-backend.log 2>&1 &)
(cd frontend && nohup pnpm dev > /tmp/cubebox-ui-frontend.log 2>&1 &)
sleep 12 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3001/
```

Expected: `307` (or `200`). Test account exists from brainstorming:
`design@cubebox.dev` / `Design-Review-2026`.

### Task 0.2: Screenshot capture script

**Files:**
- Create: `frontend/scripts/dev/capture-screens.mjs`

- [ ] **Step 1:** Write the script. Auth: register a dedicated account on
      first run (fresh worktree DBs have no users — never assume an
      account exists), then reuse Playwright `storageState` so the other
      7 invocations skip the login flow entirely (decouples the harness
      from login-page churn during the redesign). Explicit paths — no
      string surgery:

```js
// Usage (from frontend/): node scripts/dev/capture-screens.mjs <stage-label>
// Captures key pages light+dark into .superpowers/screens/<stage-label>/
import { chromium } from '@playwright/test'
import { mkdirSync, existsSync } from 'node:fs'

const BASE = process.env.BASE_URL ?? 'http://127.0.0.1:3001'
const stage = process.argv[2] ?? 'adhoc'
const root = new URL('../../../', import.meta.url).pathname
const outDir = `${root}.superpowers/screens/${stage}/`
const stateFile = `${root}.superpowers/screens/.auth-state.json`
mkdirSync(outDir, { recursive: true })

const EMAIL = 'screens@cubebox.dev'
const PASSWORD = 'Screens-Harness-2026'

const browser = await chromium.launch()
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  ...(existsSync(stateFile) ? { storageState: stateFile } : {}),
})
const page = await ctx.newPage()

// establish session: storageState → login → register (first run, fresh DB)
await page.goto(`${BASE}/`)
if (!/\/w\//.test(page.url())) {
  await page.goto(`${BASE}/login`)
  await page.getByRole('textbox', { name: 'Email' }).fill(EMAIL)
  await page.getByRole('textbox', { name: 'Password' }).fill(PASSWORD)
  await page.getByRole('button', { name: /sign in/i }).click()
  await page.waitForLoadState('networkidle')
  if (!/\/w\//.test(page.url())) {
    await page.goto(`${BASE}/register`)
    await page.getByRole('textbox', { name: 'Email' }).fill(EMAIL)
    await page.getByRole('textbox', { name: 'Password' }).fill(PASSWORD)
    await page.getByRole('button', { name: /create account/i }).click()
    await page.waitForURL(/\/w\//)
  }
  await ctx.storageState({ path: stateFile })
}
const wsUrl = new URL(page.url()).pathname // /w/<wsId>

const PAGES = [
  ['chat-home', wsUrl],
  ['ws-skills', `${wsUrl}/skills`],
  ['ws-settings', `${wsUrl}/settings?tab=workspace`],
  ['admin-models', '/admin/models'],
  ['admin-members', '/admin/members'],
  ['login-page', '/login'], // captured last: leaves session cookies intact
]

for (const theme of ['light', 'dark']) {
  await page.evaluate((t) => localStorage.setItem('theme', t), theme)
  for (const [name, path] of PAGES) {
    await page.goto(`${BASE}${path}`)
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: `${outDir}/${name}-${theme}.png` })
  }
}
await browser.close()
console.log(`screens -> ${outDir}`)
```

- [ ] **Step 2:** Run `node scripts/dev/capture-screens.mjs 0-baseline`
      from `frontend/` (resolves `@playwright/test` from the workspace
      root devDependency). Expected: 12 PNGs in
      `.superpowers/screens/0-baseline/`. These are the "before"
      reference.
- [ ] **Step 3:** `/code-review` (medium) on the script; fix; commit:

```bash
git add frontend/scripts/dev/capture-screens.mjs
git commit -m "chore(ui): screenshot capture harness for redesign stages"
```

---

## Stage 1: Token foundation (colors, fonts, theme default)

### Task 1.1: Geist fonts via npm package

**Files:**
- Modify: `frontend/packages/web/package.json` (via pnpm)
- Modify: `frontend/packages/web/app/layout.tsx`

- [ ] **Step 1:** `cd frontend && pnpm --filter web add geist`
- [ ] **Step 2:** Rewrite `app/layout.tsx` font wiring (NOT
      next/font/google — no build-time network fetch):

```tsx
import type { Metadata } from 'next'
import { GeistSans } from 'geist/font/sans'
import { GeistMono } from 'geist/font/mono'
import { NextIntlClientProvider } from 'next-intl'
import { getLocale, getMessages } from 'next-intl/server'
import { ThemeProvider } from 'next-themes'
import './globals.css'

export const metadata: Metadata = {
  title: 'cubebox',
  description: 'AI Agent System',
  icons: { icon: '/icon.svg' },
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const locale = await getLocale()
  const messages = await getMessages()
  return (
    <html
      lang={locale}
      suppressHydrationWarning
      className={`${GeistSans.variable} ${GeistMono.variable}`}
    >
      <body className="font-sans">
        <NextIntlClientProvider locale={locale} messages={messages}>
          <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
            {children}
          </ThemeProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  )
}
```

(`defaultTheme="system" enableSystem` is the theme-default migration from
the spec — done here because it's the same file.)

- [ ] **Step 3:** `pnpm --filter web build` — expected: success, no
      Google Fonts fetch in output.

### Task 1.2: Token layer in globals.css

**Files:**
- Modify: `frontend/packages/web/app/globals.css`

- [ ] **Step 1:** Replace the `@theme` block and `.dark` overrides.
      Strategy: keep the EXISTING shadcn semantic names (background,
      card, primary, muted, border, ring…) so all `ui/` components
      inherit; add new tokens for surfaces, statuses, motion, fonts.
      Dark values per spec §1; light mirrors. Full replacement for the
      token sections:

```css
@theme {
  /* fonts: Geist + CJK fallback chain */
  --font-sans:
    var(--font-geist-sans), -apple-system, 'PingFang SC', 'Microsoft YaHei',
    'Noto Sans CJK SC', sans-serif;
  --font-mono: var(--font-geist-mono), ui-monospace, 'SFMono-Regular', monospace;

  /* light mode (mirror of dark structure) */
  --color-background: #ffffff;
  --color-foreground: #171717;
  --color-card: #fafafa;            /* panel surface */
  --color-card-foreground: #171717;
  --color-raised: #f5f5f5;          /* input bar, user bubble, hover fills */
  --color-sunken: #fafafa;          /* code blocks, terminal */
  --color-primary: #0070f3;         /* THE accent */
  --color-primary-foreground: #ffffff;
  --color-secondary: #f5f5f5;
  --color-secondary-foreground: #171717;
  --color-muted: #f5f5f5;
  --color-muted-foreground: #666666;
  --color-faint: #999999;
  --color-accent: #f0f0f0;          /* hover token (shadcn hover slot) */
  --color-accent-foreground: #171717;
  --color-popover: #ffffff;
  --color-popover-foreground: #171717;
  --color-border: #eaeaea;
  --color-border-strong: #d4d4d4;
  --color-input: #eaeaea;
  --color-ring: #0070f3;

  /* status sets: surface / border / fg / solid */
  --color-success-surface: #e6f6ee;
  --color-success-border: #b3e6cc;
  --color-success-fg: #0f7b3f;
  --color-success-solid: #17a35a;
  --color-warning-surface: #fff7e6;
  --color-warning-border: #ffe1a6;
  --color-warning-fg: #925f00;
  --color-warning-solid: #f5a623;
  --color-danger-surface: #fdebeb;
  --color-danger-border: #f5c2c2;
  --color-danger-fg: #c22929;
  --color-danger-solid: #e5484d;
  --color-info-surface: #e9f2fe;
  --color-info-border: #c0dcfb;
  --color-info-fg: #0a5cc2;
  --color-info-solid: #3b82f6;

  /* shadcn `destructive` slot — 91 component files use bg-destructive /
     text-destructive / aria-invalid:border-destructive and it is NOT
     defined today (pre-existing dead classes). Map it onto danger: */
  --color-destructive: #e5484d;
  --color-destructive-foreground: #ffffff;

  /* shape */
  --radius-xs: 4px;   /* badges, chips */
  --radius: 6px;      /* buttons, inputs, cards */
  --radius-lg: 10px;  /* panels, modals */

  /* motion — NOTE the namespace: Tailwind 4 generates duration-* utilities
     from --transition-duration-*, NOT from --duration-* (verified by
     compiling with the repo's tailwindcss; --duration-* silently emits
     nothing). The duplicate plain vars below exist for var() references
     in handwritten CSS (keyframes, panel transition). */
  --transition-duration-fast: 120ms;
  --transition-duration-base: 200ms;
  --transition-duration-slow: 300ms;
  --ease-out-quart: cubic-bezier(0.16, 1, 0.3, 1);
}

@layer base {
  .dark {
    --color-background: #000000;
    --color-foreground: #ededed;
    --color-card: #0a0a0a;
    --color-card-foreground: #ededed;
    --color-raised: #111111;
    --color-sunken: #050505;
    --color-primary: #0070f3;
    --color-primary-foreground: #ffffff;
    --color-secondary: #111111;
    --color-secondary-foreground: #ededed;
    --color-muted: #111111;
    --color-muted-foreground: #a1a1a1;
    --color-faint: #666666;
    --color-accent: #161616;
    --color-accent-foreground: #ededed;
    --color-popover: #0a0a0a;
    --color-popover-foreground: #ededed;
    --color-border: #1f1f1f;
    --color-border-strong: #333333;
    --color-input: #1f1f1f;
    --color-ring: #0070f3;

    --color-success-surface: rgba(23, 163, 90, 0.12);
    --color-success-border: rgba(23, 163, 90, 0.35);
    --color-success-fg: #3dd68c;
    --color-success-solid: #17a35a;
    --color-warning-surface: rgba(245, 166, 35, 0.12);
    --color-warning-border: rgba(245, 166, 35, 0.35);
    --color-warning-fg: #f5b95e;
    --color-warning-solid: #f5a623;
    --color-danger-surface: rgba(229, 72, 77, 0.12);
    --color-danger-border: rgba(229, 72, 77, 0.35);
    --color-danger-fg: #ff6369;
    --color-danger-solid: #e5484d;
    --color-info-surface: rgba(59, 130, 246, 0.12);
    --color-info-border: rgba(59, 130, 246, 0.35);
    --color-info-fg: #52a8ff;
    --color-info-solid: #3b82f6;

    --color-destructive: #e5484d;
    --color-destructive-foreground: #ffffff;
  }
}
```

- [ ] **Step 1b:** Animation utilities dependency — `animate-in`,
      `fade-in`, `slide-in-from-bottom`, `zoom-in-*` used by existing
      `ui/` components (and later stages) are tw-animate-css utilities,
      and the package is NOT installed: those classes compile to NOTHING
      today (pre-existing silent no-op). Fix:

```bash
pnpm --filter web add -D tw-animate-css
```

      and in `globals.css`, after `@import 'tailwindcss';` add
      `@import 'tw-animate-css';`. Verify: `pnpm --filter web build`,
      then grep the built CSS for `animate-in` — must now produce rules.

Notes for the engineer:
- Tailwind 4 generates `bg-raised`, `border-border-strong`,
  `text-success-fg`, `bg-warning-surface`, `rounded-xs`,
  `duration-fast` (from `--transition-duration-fast`) etc.
  automatically.
- Keep everything else in globals.css (resizable-panel fix,
  scrollbar-none, `@custom-variant dark`, hljs palettes). The light hljs
  palette stays; in Stage 4 the dark hljs bg moves onto `--color-sunken`.
- Light-mode dark-value mirroring was eyeballed for WCAG AA; verify
  contrast in Step 3 and tune in place (`globals.css` is the source of
  truth — do NOT back-port into the spec).
- **`accent` is the hover slot, but today it is ALSO used for
  selected/open states** with no other indicator: `bg-accent/70`
  selected file rows in `workspace-settings/skills/WorkspaceSkillDetail.tsx`
  + `admin/skills/SkillDetailPanel.tsx`, `data-popup-open:bg-accent` in
  `ui/dropdown-menu.tsx`. With the new darker accent these become
  indistinguishable from hover. Stages 4/5 carry an explicit sweep:
  selected rows → `bg-raised` + the 2px `before:bg-primary` indicator;
  audit via `grep -rn "bg-accent" components app --include='*.tsx' | grep -v "hover:"`.

- [ ] **Step 2:** Global utility adjustments in the same file: add
      tabular numerals helper + reduced motion guard at the end:

```css
@utility tabular-nums {
  font-variant-numeric: tabular-nums;
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 3:** Visual check: `node scripts/dev/capture-screens.mjs
      1-tokens`. Expected: dark = pure black base + new grays; light =
      white/`#eaeaea`. Old hardcoded color islands (amber/blue cards)
      WILL still show — accepted mixed state per spec.
- [ ] **Step 4:** Commit: `feat(ui): stage1: token foundation — Geist +
      Vercel Mono palette + status token sets`

### Task 1.3: Theme toggle migration (resolvedTheme)

**Files:**
- Modify: `frontend/packages/web/components/ui/theme-toggle.tsx`
- Modify: `frontend/packages/web/components/sidebar/AvatarPopover.tsx:98`
  (same pattern)
- Test: `frontend/packages/web/__tests__/components/ThemeToggle.test.tsx` (create)

- [ ] **Step 1:** Write the failing test. Assert the OBSERVABLE outcome
      (html class set by next-themes), not its private localStorage
      format; stub matchMedia via `vi.stubGlobal` and restore it so the
      mutation can't leak into sibling tests:

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from 'next-themes'
import { NextIntlClientProvider } from 'next-intl'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import { afterEach, describe, expect, it, vi } from 'vitest'

// system resolves dark; first click must flip to LIGHT (uses resolvedTheme)
describe('ThemeToggle under theme=system', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('first click flips against resolvedTheme, not raw theme', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('dark'),
      media: q, addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
      dispatchEvent: () => false, onchange: null,
    }))
    render(
      <NextIntlClientProvider locale="en" messages={{ avatar: { lightTheme: 'Light', darkTheme: 'Dark' } }}>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <ThemeToggle />
        </ThemeProvider>
      </NextIntlClientProvider>,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(document.documentElement.classList.contains('light')).toBe(true)
  })
})
```

- [ ] **Step 2:** Run
      `pnpm --filter web test -- ThemeToggle` — expected FAIL (current
      code reads `theme`, which is `'system'`, so it sets `'dark'`).
- [ ] **Step 3:** Fix both call sites — replace
      `const { theme, setTheme } = useTheme()` with
      `const { resolvedTheme, setTheme } = useTheme()` and every
      `theme === 'dark'` comparison with `resolvedTheme === 'dark'`
      (label + icon + onClick in `theme-toggle.tsx`; the menu item in
      `AvatarPopover.tsx`).
- [ ] **Step 4:** Run the test — expected PASS. Run
      `pnpm --filter web test` for regressions.
- [ ] **Step 5:** Commit: `feat(ui): stage1: theme default follows
      system; toggles read resolvedTheme`

### Task 1.4: Raw-color eslint guard (lands NOW, not in Stage 7)

**Files:**
- Modify: `frontend/packages/web/eslint.config.mjs`

Landing the guard first means every later stage's `pnpm lint` gate
catches new raw colors at the door, instead of Stage 7 discovering five
stages of accumulated drift. Existing offenders go in a temporary
allowlist that shrinks per stage and must be EMPTY by Stage 7.

- [ ] **Step 1:** Generate the current-offender list:

```bash
grep -rlE "(bg|text|border|ring|divide|from|to)-(amber|blue|green|red|emerald|sky|yellow|purple|pink|orange|indigo|violet|teal|cyan|lime|rose|fuchsia|slate|gray|zinc|neutral|stone)-[0-9]" \
  packages/web/components packages/web/app --include='*.tsx' | sort
```

- [ ] **Step 2:** Add to `eslint.config.mjs` (two selectors: plain string
      literals AND template-literal chunks — template literals are how
      raw colors usually sneak past `Literal`-only guards):

```js
// --- redesign color guard (docs/dev/specs/2026-06-10-ui-redesign-design.md §1)
const RAW_PALETTE =
  '(?:bg|text|border|ring|divide|from|to)-(?:amber|blue|green|red|emerald|sky|yellow|purple|pink|orange|indigo|violet|teal|cyan|lime|rose|fuchsia|slate|gray|zinc|neutral|stone)-[0-9]'

const rawColorGuard = {
  files: ['components/**/*.tsx', 'app/**/*.tsx'],
  ignores: [
    'components/chat/widget/**', // iframe srcdoc: literal hex is structural (see spec)
    // TEMP ALLOWLIST — paste Step 1 output here; each stage deletes the
    // files it cleans; MUST be empty by Stage 7 Task 7.2.
  ],
  rules: {
    'no-restricted-syntax': [
      'error',
      {
        selector: `Literal[value=/${RAW_PALETTE}/]`,
        message: 'Raw palette utilities are banned — use semantic tokens (spec §1).',
      },
      {
        selector: `TemplateElement[value.raw=/${RAW_PALETTE}/]`,
        message: 'Raw palette utilities are banned — use semantic tokens (spec §1).',
      },
    ],
  },
}
```

      (append `rawColorGuard` to the exported config array).
- [ ] **Step 3:** `pnpm --filter web lint` — green with the allowlist in
      place. Negative test: add `bg-amber-500` to a non-allowlisted file
      inside a plain string AND inside a template literal — both must
      fail; revert.
- [ ] **Step 4:** Commit: `feat(ui): stage1: raw-color eslint guard with
      shrinking allowlist`

### Task 1.5: Stage gate

- [ ] `pnpm -r type-check && pnpm --filter web lint && pnpm --filter web test` — all green.
- [ ] E2E smoke (from `frontend/`): `pnpm exec playwright test
      packages/web/__tests__/e2e/chat-flow.spec.ts` (worktree DB
      auto-routed). Expected: PASS — no copy/selector changed yet; the
      system-theme default resolves light under chromium's default
      colorScheme, so existing specs are unaffected.
- [ ] `node scripts/dev/capture-screens.mjs 1-tokens`
- [ ] `/code-review` (medium) on the stage diff; fix actionable findings; commit fixes.

---

## Stage 2: UI primitives

### Task 2.1: Restyle the 25 `components/ui/` primitives

**Files:** every file in `frontend/packages/web/components/ui/` (25
files: accordion, alert, alert-dialog, badge, button, card, checkbox,
collapsible, combobox, dropdown-menu, input, input-group, label, popover,
radio-group, resizable, scroll-area, select, separator, switch, table,
tabs, textarea, theme-toggle, tooltip)

The token swap in Stage 1 already recolors them. This task fixes shape +
states. Apply this transformation table to every file:

| Find | Replace with | Why |
|---|---|---|
| `rounded-md`, `rounded-lg` on buttons/inputs/cards | `rounded` (6px via `--radius`) | 3-step radius scale |
| `rounded-[min(var(--radius-md),10px)]` (button xs/sm) | `rounded` | kill arbitrary values |
| `rounded-full` — **ONLY in `badge.tsx`** | `rounded-xs` | square-ish badges per direction B. Do NOT touch the functional circles: `switch.tsx` (track+thumb), `radio-group.tsx` (circle+dot), `resizable.tsx` (drag handle) — those stay `rounded-full` |
| `rounded-xl`, `rounded-2xl` on cards/popovers | `rounded-lg` (10px) | panels/modals step |
| `transition-colors` without duration | `transition-colors duration-fast` | motion tokens |
| any `focus-visible:ring-*` missing | `focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none` | visible focus everywhere |
| `text-[0.8rem]`, `text-[13px]` | `text-sm` (13px) | semantic type scale |
| `text-[11px]`, `text-[9px]`, `text-[10px]` | `text-2xs` (11px) | semantic type scale |

- [ ] **Step 1:** Add the type-scale override in `globals.css` `@theme`.
      **CRITICAL — do NOT override `--text-base`**: it must stay 16px.
      `input.tsx`/`textarea.tsx` use `text-base … md:text-sm` precisely
      so mobile inputs are ≥16px (iOS Safari auto-zooms on focus below
      16px — shrinking it would break the Stage 6 mobile work), and ~24
      dialog/section titles use `text-base font-semibold` for 16px
      hierarchy. Chat prose uses `prose prose-sm` (typography plugin,
      independent of these tokens) and is untouched. The scale
      11/12/13/14/16/20/24 maps as: `text-2xs`(new)/`text-xs`/`text-sm`/
      `text-md`(new)/`text-base`(default)/`text-xl`/`text-2xl`:

```css
  --text-2xs: 11px;
  --text-2xs--line-height: 1.45;
  /* --text-xs stays default 12px */
  --text-sm: 13px;
  --text-sm--line-height: 1.55;
  --text-md: 14px;
  --text-md--line-height: 1.55;
  /* --text-base stays default 16px — iOS anti-zoom + title hierarchy */
  --text-xl: 20px;
  --text-xl--line-height: 1.35;
  --text-2xl: 24px;
  --text-2xl--line-height: 1.25;
```

  UI chrome standardizes on `text-sm` (13px, direction B's UI size);
  `text-lg` is unused in the new scale — don't introduce it.

- [ ] **Step 2:** Apply the table file-by-file. For `button.tsx`
      additionally add a pressed state to the base CVA string:
      `active:scale-[0.98] active:transition-transform`.
- [ ] **Step 3:** `pnpm --filter web test && pnpm --filter web build` —
      green.
- [ ] **Step 4:** Commit: `feat(ui): stage2: primitives on token
      radius/type/motion scale + universal focus rings`

### Task 2.2: Add `ui/sheet.tsx` (slide-over primitive)

**Files:**
- Create: `frontend/packages/web/components/ui/sheet.tsx`

- [ ] **Step 1:** Do NOT use the shadcn CLI and do NOT vendor a Radix
      sheet: this codebase's primitives are **@base-ui/react**, which
      emits `data-open`/`data-closed` attributes — Radix-style
      `data-[state=open]:` selectors would never match. (Also note
      `pnpm --filter web dlx` does not change cwd — dlx ignores
      `--filter` — so the CLI invocation would run against the wrong
      directory anyway.) Instead, build `ui/sheet.tsx` by hand from the
      in-repo dialog pattern: copy the structure of
      `components/ui/alert-dialog.tsx` (base-ui Dialog: Root/Trigger/
      Portal/Backdrop/Popup parts) and restyle:
      - Backdrop: `fixed inset-0 bg-background/80 backdrop-blur-sm
        data-open:animate-in data-open:fade-in
        data-closed:animate-out data-closed:fade-out duration-base`
      - Popup (side="right" default): `fixed inset-y-0 right-0 z-50
        w-[480px] max-w-[90vw] border-l border-border bg-card shadow-lg
        outline-none data-open:animate-in
        data-open:slide-in-from-right data-closed:animate-out
        data-closed:slide-out-to-right duration-slow
        ease-[var(--ease-out-quart)] flex flex-col`
      - Add a `side?: 'right' | 'left'` prop (left variant flips border
        and slide direction — Stage 6's mobile drawer uses it)
      - Export: `Sheet`, `SheetTrigger`, `SheetContent`, `SheetHeader`,
        `SheetTitle`, `SheetDescription`, `SheetFooter`, `SheetClose` —
        mirroring alert-dialog's export names/pattern
- [ ] **Step 2:** Smoke-test in the running app behind a scratch page or
      Storybook-less manual mount; verify open/close animates (Task 1.2
      Step 1b installed tw-animate-css — without it these classes are
      no-ops).
- [ ] **Step 3:** `pnpm --filter web build` green. Commit:
      `feat(ui): stage2: add base-ui sheet primitive for slide-over forms`

### Task 2.3: Add toast system (sonner)

**Files:**
- Modify: `frontend/packages/web/package.json` (pnpm add)
- Create: `frontend/packages/web/components/ui/sonner.tsx`
- Modify: `frontend/packages/web/app/layout.tsx` (mount `<Toaster />`)
- Create: `frontend/packages/web/hooks/useUndoableDelete.ts` (hooks live
  in `hooks/`, NOT `lib/` — 18 existing hooks + the components.json
  `hooks` alias establish the convention)
- Test: `frontend/packages/web/__tests__/hooks/useUndoableDelete.test.ts`

- [ ] **Step 1:** `pnpm --filter web add sonner`
- [ ] **Step 2:** `components/ui/sonner.tsx`:

```tsx
'use client'

import { useTheme } from 'next-themes'
import { Toaster as Sonner } from 'sonner'

export function Toaster(props: React.ComponentProps<typeof Sonner>) {
  const { resolvedTheme } = useTheme()
  return (
    <Sonner
      theme={resolvedTheme === 'dark' ? 'dark' : 'light'}
      position="bottom-right"
      toastOptions={{
        classNames: {
          toast: 'bg-raised border border-border-strong text-foreground rounded-lg shadow-lg',
          actionButton: 'bg-primary text-primary-foreground',
        },
      }}
      {...props}
    />
  )
}
```

      Mount in `app/layout.tsx` inside ThemeProvider, after `{children}`:
      `<Toaster />` (import from `@/components/ui/sonner`).
- [ ] **Step 3:** Write failing test for the undo hook:

```ts
import { renderHook, act } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useUndoableDelete } from '@/hooks/useUndoableDelete'

describe('useUndoableDelete', () => {
  it('commits after the grace window unless undone', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const { result } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-1', commit, { label: 'Deleted', actionLabel: 'Undo' }))
    expect(commit).not.toHaveBeenCalled()          // delayed
    act(() => vi.advanceTimersByTime(5000))
    expect(commit).toHaveBeenCalledTimes(1)        // committed
    vi.useRealTimers()
  })

  it('does not commit when undone within the window', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const { result } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-2', commit, { label: 'Deleted', actionLabel: 'Undo' }))
    act(() => result.current.undo('item-2'))
    act(() => vi.advanceTimersByTime(5000))
    expect(commit).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('FLUSHES (commits) pending deletes on unmount — never cancels them', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const { result, unmount } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-3', commit, { label: 'Deleted', actionLabel: 'Undo' }))
    unmount() // user navigated away inside the grace window
    expect(commit).toHaveBeenCalledTimes(1) // the delete the toast promised still happens
    vi.useRealTimers()
  })
})
```

- [ ] **Step 4:** Run it — FAIL (module missing). Implement. Two
      deliberate properties: the Map stores the COMMIT alongside the
      timer (so unmount can flush, not just cancel — a cancelled timer
      would mean "Deleted" toasts that silently never delete), and both
      toast strings come in as required params (the hook hardcodes no
      English — call sites pass `t('common.deleted')` /
      `t('common.undo')`):

```ts
'use client'

import { useCallback, useEffect, useRef } from 'react'
import { toast } from 'sonner'

const UNDO_WINDOW_MS = 5000

interface PendingDelete {
  timer: ReturnType<typeof setTimeout>
  commit: () => void | Promise<void>
}

interface UndoableDeleteOpts {
  /** translated toast text, e.g. t('common.deleted') */
  label: string
  /** translated action text, e.g. t('common.undo') */
  actionLabel: string
  onUndo?: () => void
}

/** Optimistic-hide + delayed-commit delete with an undo toast.
 *  Unmount FLUSHES pending commits (the UI already promised deletion). */
export function useUndoableDelete() {
  const pending = useRef(new Map<string, PendingDelete>())

  const undo = useCallback((id: string) => {
    const entry = pending.current.get(id)
    if (entry) {
      clearTimeout(entry.timer)
      pending.current.delete(id)
    }
  }, [])

  const requestDelete = useCallback(
    (id: string, commit: () => void | Promise<void>, opts: UndoableDeleteOpts) => {
      const timer = setTimeout(() => {
        pending.current.delete(id)
        void commit()
      }, UNDO_WINDOW_MS)
      pending.current.set(id, { timer, commit })
      toast(opts.label, {
        duration: UNDO_WINDOW_MS,
        action: {
          label: opts.actionLabel,
          onClick: () => {
            undo(id)
            opts.onUndo?.()
          },
        },
      })
    },
    [undo],
  )

  useEffect(() => {
    const map = pending.current
    return () => {
      // flush, don't cancel: commit everything still pending
      for (const { timer, commit } of map.values()) {
        clearTimeout(timer)
        void commit()
      }
      map.clear()
    }
  }, [])

  return { requestDelete, undo }
}
```

      Caller contract (see Stage 5 prerequisites — the current delete
      flows are API-first and need rework before this hook is wirable):
      hide the item optimistically, call
      `requestDelete(id, commitFn, { label: t('common.deleted'),
      actionLabel: t('common.undo'), onUndo: restoreFn })`. Add keys
      `common.deleted` / `common.undo` to `messages/en.json` + `zh.json`
      in this task.
- [ ] **Step 5:** Tests PASS. `pnpm --filter web build` green.
- [ ] **Step 6:** Commit: `feat(ui): stage2: sonner toaster + undoable
      delete hook`

### Task 2.4: Stage gate

- [ ] `pnpm -r type-check && pnpm --filter web lint && pnpm --filter web test`
- [ ] `node scripts/dev/capture-screens.mjs 2-primitives`
- [ ] `/code-review` (medium); fix; commit.

---

## Stage 3: Panel Shell (structural — high effort review)

### Task 3.1: Generalize PanelHeader

**Files:**
- Modify: `frontend/packages/web/components/panel/PanelHeader.tsx`
- Test: `frontend/packages/web/__tests__/components/PanelHeader.test.tsx`
  (create; none exists today)

New prop contract — tool-coupled props become one of two shapes, plus an
action slot:

```tsx
'use client'

import { useState, type ReactNode } from 'react'
import { X, Copy, Check, Plug, Maximize2, Minimize2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { getToolIcon, getParamSummary } from '@/lib/toolIcons'
import { useMcpToolRegistryStore } from '@cubebox/core'

interface ToolHeaderSource {
  kind: 'tool'
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
}

interface PlainHeaderSource {
  kind: 'plain'
  icon: ReactNode
  title: string
  /** mono subtitle: command, path, url … */
  subtitle?: string
  copyText?: string
}

interface PanelHeaderProps {
  source: ToolHeaderSource | PlainHeaderSource
  /** per-adapter extras (version popover, download, take-over…) rendered before the standard actions */
  actions?: ReactNode
  fullscreen?: { active: boolean; onToggle: () => void }
  onClose: () => void
}

export function PanelHeader({ source, actions, fullscreen, onClose }: PanelHeaderProps) {
  const t = useTranslations('panel.header')
  const [copied, setCopied] = useState(false)
  const mcpEntry = useMcpToolRegistryStore((s) =>
    source.kind === 'tool' ? s.lookup(source.toolName) : null,
  )

  let icon: ReactNode
  let title: string
  let subtitle: string | undefined
  let copyText: string | undefined

  if (source.kind === 'tool') {
    const displayName = mcpEntry?.bare_name ?? source.toolName
    const mcpIconSrc = mcpEntry
      ? (mcpEntry.tool_icons[0]?.src ?? mcpEntry.server_icons[0]?.src ?? null)
      : null
    const FallbackIcon = getToolIcon(displayName)
    icon = mcpIconSrc ? (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={mcpIconSrc} alt="" className="size-3.5 rounded-xs shrink-0 object-contain" />
    ) : mcpEntry ? (
      <Plug className="size-3.5 text-muted-foreground shrink-0" />
    ) : (
      /* eslint-disable-next-line react-hooks/static-components */
      <FallbackIcon className="size-3.5 text-muted-foreground shrink-0" />
    )
    title = displayName
    subtitle = getParamSummary(displayName, source.toolArgs, 40) || undefined
    copyText = source.toolResult ?? JSON.stringify(source.toolArgs, null, 2)
  } else {
    icon = source.icon
    title = source.title
    subtitle = source.subtitle
    copyText = source.copyText
  }

  const handleCopy = async () => {
    if (!copyText) return
    await navigator.clipboard.writeText(copyText)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <header className="h-11 border-b border-border flex items-center gap-2 px-4 shrink-0 bg-card">
      {icon}
      <span className="text-sm font-medium text-foreground shrink-0" title={title}>
        {title}
      </span>
      {subtitle && <span className="font-mono text-xs text-muted-foreground truncate">{subtitle}</span>}
      <span className="ml-auto flex items-center gap-1">
        {actions}
        {copyText !== undefined && (
          <button
            onClick={handleCopy}
            className="p-1 rounded-xs hover:bg-accent transition-colors duration-fast"
            title={t('copy')}
          >
            {copied ? (
              <Check className="size-3.5 text-success-fg" />
            ) : (
              <Copy className="size-3.5 text-muted-foreground" />
            )}
          </button>
        )}
        {fullscreen && (
          <button
            onClick={fullscreen.onToggle}
            className="p-1 rounded-xs hover:bg-accent transition-colors duration-fast"
            title={t(fullscreen.active ? 'exitFullscreen' : 'fullscreen')}
          >
            {fullscreen.active ? (
              <Minimize2 className="size-3.5 text-muted-foreground" />
            ) : (
              <Maximize2 className="size-3.5 text-muted-foreground" />
            )}
          </button>
        )}
        <button
          onClick={onClose}
          className="p-1 rounded-xs hover:bg-accent transition-colors duration-fast"
          title={t('close')}
        >
          <X className="size-3.5 text-muted-foreground" />
        </button>
      </span>
    </header>
  )
}
```

- [ ] **Step 1:** Write failing tests first (render `kind: 'tool'` with a
      known tool keeps old behavior: name + summary + copy; render
      `kind: 'plain'` with custom actions renders the action node;
      fullscreen toggle fires).
- [ ] **Step 2:** Run — FAIL (props don't exist). Implement as above.
- [ ] **Step 3:** Update the one existing caller `ToolDetailPanel.tsx`:
      `<PanelHeader source={{ kind: 'tool', toolName, toolArgs, toolResult }} onClose={...} />`.
- [ ] **Step 4:** Add i18n keys `panel.header.fullscreen` /
      `panel.header.exitFullscreen` (en+zh). Tests PASS; typecheck green.
- [ ] **Step 5:** Commit: `feat(ui): stage3: PanelHeader generalized
      (tool/plain sources, action slot, fullscreen)`

### Task 3.2: Fold the 4 sibling panels under the shared header

**Files:**
- Modify: `frontend/packages/web/components/panel/artifact/ArtifactPanel.tsx`
  (NOTE the `artifact/` subdirectory — AppShell imports
  `@/components/panel/artifact/ArtifactPanel`; do not create a new file
  at `components/panel/ArtifactPanel.tsx`. Delete its copy-pasted header
  markup; render `PanelHeader` with `kind: 'plain'`,
  `actions={<VersionPopover…/><DownloadLink…/>}`)
- Modify: `frontend/packages/web/components/panel/BrowserView.tsx`
  (header → `PanelHeader` with `actions={takeOver/handBack + refresh}`)
- Modify: `frontend/packages/web/components/panel/SkillCandidatePanel.tsx`
- Modify: `frontend/packages/web/components/panel/AttachmentPreviewView.tsx`
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`
  (no dispatch change — only ensure all 4 panels render inside the same
  scroll/padding container classes: `flex-1 min-h-0 overflow-auto`)

- [ ] **Step 1:** For each panel: extract its existing header JSX
      contents into `PanelHeader` props; move panel-specific buttons into
      `actions`. Read each file first; preserve all handlers verbatim.
      No behavior change — this is a markup fold.
- [ ] **Step 2:** `pnpm --filter web test` + manually exercise in the
      running app: open a tool result, an artifact, browser view —
      headers identical structure, panel-specific actions present.
- [ ] **Step 3:** Commit per panel (4 small commits):
      `refactor(ui): stage3: <Panel> onto shared PanelHeader`

### Task 3.3: Panel open/close transition (gated, drag-safe)

**Files:**
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`
- Modify: `frontend/packages/web/app/globals.css` (one rule)

- [ ] **Step 1:** Add CSS (globals.css). Inverted gating — the
      transition is ON by default and a `panel-dragging` class disables
      it during pointer drags. No JS timers, no duration duplicated in
      JS, no re-entrancy bug when the user spams open/close:

```css
/* Panel open/close: sanctioned width transition (spec motion exemption).
   Disabled while the user drags the divider so resize stays 1:1. */
[data-slot='resizable-panel-group']:not(.panel-dragging)
  [data-slot='resizable-panel'] {
  transition: flex-basis var(--transition-duration-slow) var(--ease-out-quart);
}
```

- [ ] **Step 2:** In `AppShell.tsx`, wire the drag state to the existing
      `ResizableHandle`: react-resizable-panels' handle exposes an
      `onDragging={(isDragging) => …}` callback — toggle a
      `panel-dragging` class on the group wrapper from it. Nothing else:
      programmatic open/close transitions automatically; drags don't.
- [ ] **Step 3:** Manual check in app: open/close eases; dragging the
      divider stays 1:1 with the cursor (no lag); rapid open/close
      mashing never sticks. Also check initial page load for a one-frame
      animation flash — if hydration causes one, add the class on mount
      and remove it in a `useEffect` after first paint.
- [ ] **Step 4:** Commit: `feat(ui): stage3: eased panel open/close,
      drag-safe`

### Task 3.4: Stage gate

- [ ] `pnpm -r type-check && pnpm --filter web lint && pnpm --filter web test`
- [ ] E2E (from `frontend/`): `pnpm exec playwright test
      packages/web/__tests__/e2e/chat-flow.spec.ts
      packages/web/__tests__/e2e/widget-shell.spec.ts`
- [ ] `node scripts/dev/capture-screens.mjs 3-panel-shell`
- [ ] `/code-review` (high — structural stage); fix; commit.

---

## Stage 4: Chat area

### Task 4.1: Sidebar restyle

**Files:**
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`
- Modify: `frontend/packages/web/components/sidebar/WorkspacesSection.tsx`
- Modify: `frontend/packages/web/components/sidebar/AvatarPopover.tsx`

Transformations (token classes only):
- Container: `bg-card border-r border-border` (keep `w-56`).
- Group labels ("Workspaces", "Recent chats"): `text-xs uppercase
  tracking-wider text-faint font-medium`.
- Conversation rows: base `text-muted-foreground hover:bg-accent
  hover:text-foreground rounded transition-colors duration-fast`;
  active: `bg-accent text-foreground` + indicator `before:absolute
  before:left-0 before:top-[22%] before:bottom-[22%] before:w-0.5
  before:rounded-full before:bg-primary` (row gets `relative`).
- Account footer: avatar (size-7 `rounded-md`) + email
  `text-sm text-muted-foreground truncate` — no plan badge.
- "New chat" button: secondary look — `bg-raised border
  border-border-strong hover:border-primary hover:text-primary
  transition-colors duration-fast`.

- [ ] **Step 1:** Apply; verify in app (hover/active/focus all visible,
      both themes).
- [ ] **Step 2:** Run sidebar-touching E2E (from `frontend/`):
      `pnpm exec playwright test packages/web/__tests__/e2e/workspace-switch.spec.ts`
- [ ] **Step 3:** Commit: `feat(ui): stage4: sidebar on token language`

### Task 4.2: Message stream

**Files:**
- Modify: `frontend/packages/web/components/chat/UserMessage.tsx`
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`
  (remove avatar block, pure typography). NOTE: there is NO separate
  `HistoryAssistantMessage.tsx` file — `HistoryAssistantMessage` is
  `memo(AssistantMessage)` exported from the same file (line ~676);
  editing AssistantMessage covers both render paths. Do not create a
  fork.
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
  (column: `max-w-[760px] mx-auto px-6`; replace `bg-amber-500/10
  border-amber-500/30` warning banner with
  `bg-warning-surface border-warning-border text-warning-fg`)
- Modify: `frontend/packages/web/components/chat/ToolCallGroup.tsx` +
  `ToolCallItem.tsx` (bordered container `border border-border
  rounded-lg bg-card divide-y divide-border`; rows: `font-mono text-sm`,
  running spinner, `text-success-fg` check / `text-danger-fg` cross)
- Modify: `frontend/packages/web/components/chat/ThinkingBadge.tsx`,
  `TokenUsageBar.tsx`, `CitationMarker.tsx`, `MemoryUpdateChip.tsx`
  (metadata chips: `font-mono text-xs text-faint border border-border
  rounded-xs px-2 py-0.5`; TokenUsageBar thresholds:
  `bg-success-solid/bg-warning-solid/bg-danger-solid` instead of
  green/amber/red-500; numbers get `tabular-nums`)
- Modify: `frontend/packages/web/lib/utils.ts` (`proseClasses`:
  `prose-pre:bg-sunken prose-pre:border prose-pre:border-border
  prose-pre:rounded-lg`)
- Modify: `frontend/packages/web/components/chat/AskUserCard.tsx`,
  `SandboxConfirmCard.tsx` (blue/amber/green/red hardcodes → info/
  warning/success/danger token sets; approve button
  `bg-success-solid hover:bg-success-solid/90 text-white`, reject
  `border-danger-border text-danger-fg hover:bg-danger-surface`)
- Modify: `frontend/packages/web/components/chat/RunErrorBubble.tsx`
  (inline bar: `border border-danger-border bg-danger-surface
  text-danger-fg rounded-lg px-3 py-2 flex items-center gap-2` — keep
  `role="alert"`, display-only; update its unit test snapshot)
- Modify: `frontend/packages/web/components/chat/FailoverBanner.tsx`
  (amber → warning set)

UserMessage core (complete component body — `text-md` = 14px, matching
the assistant's `prose-sm` body size so the two message types read at the
same scale):

```tsx
export function UserMessage({ children, attachments }: UserMessageProps) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[78%] rounded-lg rounded-br-xs border border-border bg-raised px-3.5 py-2.5 text-md leading-relaxed">
        {children}
        {attachments}
      </div>
    </div>
  )
}
```

(Adapt prop names to the existing file — read it first; only the
className set changes plus dropping the saturated `bg-primary`.)

Also in this task — the `bg-accent` selected-state sweep for chat-area
files (see Stage 1 note): any non-hover `bg-accent`/`bg-accent/70`
selected state in files this task touches → `bg-raised` + 2px
`before:bg-primary` indicator.

- [ ] **Step 1:** Apply file-by-file; after each, eyeball in the running
      app with a real conversation (`design@cubebox.dev` workspace has
      one; send new messages as needed).
- [ ] **Step 2:** `pnpm --filter web test` (RunErrorBubble + any
      snapshot updates).
- [ ] **Step 3:** Commit in 3 chunks: bubbles+stream, tool-call group,
      status cards: `feat(ui): stage4: <chunk>`

### Task 4.3: Input bar (V1 layout + placeholder + E2E selectors)

**Files:**
- Modify: `frontend/packages/web/components/layout/InputBar.tsx`
- Modify: `frontend/packages/web/messages/en.json` + `messages/zh.json`
- Modify (selectors): exactly SIX e2e spec files use the placeholder —
  `frontend/packages/web/__tests__/e2e/chat-flow.spec.ts`,
  `streaming.spec.ts`, `steering.spec.ts`, `workspace-switch.spec.ts`,
  `memory-reflection.spec.ts`, `i18n.spec.ts` (verified; there is no
  seventh — `attachments.spec.ts` uses `getByTestId('chat-input')` and
  needs no change). Re-run the Step 1 grep to confirm before editing.
- Test first: `frontend/packages/web/__tests__/components/InputBar.test.tsx`
  (extend if exists, create otherwise)

- [ ] **Step 1:** Grep to enumerate every selector usage:

```bash
grep -rn "How can I help you\|有什么可以帮你的" frontend/packages/web --include='*.ts*' --include='*.json'
```

- [ ] **Step 2:** i18n: `chat.placeholder` en → `"Describe a task…"`,
      zh → `"描述一个任务…"`. Keep `pendingHitlLock` untouched.
- [ ] **Step 3:** Layout change in `InputBar.tsx` (read file first; it
      holds streaming/steer logic — DO NOT touch handlers, `showStop`
      logic, PendingSteers, attachments, dropzone): the current row
      above the textarea contains THREE components — `PresetPicker`,
      `ThinkingControl`, **and `ThinkingBadge`** (lines ~263-268; the
      existing InputBar unit test asserts the badge renders whenever
      thinking is non-off — keep it). Move all three into the internal
      bottom toolbar: badge + preset + thinking selectors right-aligned
      before the send button; attach button stays left. Container:
      `bg-raised border border-border-strong rounded-lg
      focus-within:border-primary focus-within:ring-2
      focus-within:ring-ring/30 transition duration-base`. Hint row
      (`Enter to send…`) stays below.
- [ ] **Step 4:** Update every E2E selector from Step 1's list to the new
      copy (`getByPlaceholder('Describe a task…')`; zh assertion in
      i18n.spec.ts → `描述一个任务…`). Prefer switching to the existing
      `getByTestId('chat-input')` where the spec file already imports
      testid helpers — mechanical replace otherwise.
- [ ] **Step 5:** Unit test: assert preset/thinking render inside the
      toolbar container (`within(toolbar).getByLabelText(...)`) and that
      typing-while-streaming still flips stop→send (cover the steer
      guard: render with `messageIsStreaming=true`, type, expect send
      button enabled).
- [ ] **Step 6:** Run: unit + the six updated E2E specs. All PASS.
- [ ] **Step 7:** Commit: `feat(ui): stage4: input bar V1 layout,
      task-oriented placeholder, selector updates`

### Task 4.4: Empty-state home + prompt cards

**Files:**
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/page.tsx` (the
  empty-state home lives at lines ~104-110: Box logo block + h1
  "cubebox" + subtitle. Do NOT grep for "AI Agent System" — that string
  only exists in layout.tsx metadata, not in the page JSX). Update
  `__tests__/components/WorkspaceHomePage.test.tsx` alongside.
- Create: `frontend/packages/web/components/chat/PromptCards.tsx`
- Create: `frontend/packages/web/hooks/useComposerDraft.ts`
- Modify: `frontend/packages/web/components/layout/InputBar.tsx` (one
  small effect — see Step 1b)
- Modify: `messages/en.json` + `messages/zh.json` (`home.promptCards.*`)

- [ ] **Step 1:** `PromptCards.tsx` — three cards (analyze a data file /
      research a topic / automate a workflow), each
      `button` with `border border-border rounded-lg bg-card p-3.5
      text-left hover:border-border-strong hover:bg-accent
      hover:-translate-y-px transition duration-fast focus-visible:ring-2
      focus-visible:ring-ring`; icon row `font-mono text-info-fg`; title
      `text-sm font-medium`; description `text-xs text-faint`.
- [ ] **Step 1b:** Input filling — RESOLVED (no fork): there is no draft
      setter in `@cubebox/core` (conversationStore's `draft` is a
      creation flag) and InputBar's `content` is internal `useState`
      that must not be hoisted (Task 4.3 forbids restructuring). Bridge
      with a tiny module-level store:

```ts
// hooks/useComposerDraft.ts
'use client'

import { create } from 'zustand'

interface ComposerDraftState {
  draft: string | null
  setDraft: (text: string) => void
  consume: () => string | null
}

export const useComposerDraft = create<ComposerDraftState>((set, get) => ({
  draft: null,
  setDraft: (text) => set({ draft: text }),
  consume: () => {
    const d = get().draft
    if (d !== null) set({ draft: null })
    return d
  },
}))
```

      PromptCards `onClick`: `useComposerDraft.getState().setDraft(text)`.
      InputBar adds ONE subscription effect (additive — no handler
      changes): `const draft = useComposerDraft((s) => s.draft)` +
      `useEffect(() => { if (draft !== null) { setContent(draft);
      useComposerDraft.getState().consume() } }, [draft])`. (zustand is
      already a dependency via the existing stores.)
- [ ] **Step 2:** Replace logo block with placeholder mark (40px square,
      `rounded-lg bg-gradient-to-br from-border-strong to-raised border
      border-border-strong grid place-items-center font-mono text-xs
      text-muted-foreground`, content `cx`).
- [ ] **Step 3:** i18n keys (en/zh) for the 3 cards' title+description.
- [ ] **Step 4:** Verify in app; commit:
      `feat(ui): stage4: empty-state home with placeholder mark + prompt cards`

### Task 4.5: Loading skeletons + streaming cursor

**Execute together with Tasks 4.1/4.2** — it edits the same two files
(Sidebar, MessageList); fold these changes into those tasks' commits
rather than a third round of edits/review on the same files.

**Files:**
- Create: `frontend/packages/web/components/ui/skeleton.tsx`
  (`bg-accent animate-pulse rounded` div)
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`
  (conversation-list loading → 5 skeleton rows matching row height)
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
  (history loading → 3 skeleton blocks: right-aligned bubble shape, two
  left text bars; streaming text: append blinking caret span when last
  block is streaming: `after:content-[''] after:inline-block after:w-[7px]
  after:h-[15px] after:bg-primary after:rounded-xs after:animate-pulse
  after:align-[-2px] after:ml-0.5`)

- [ ] **Step 1:** Implement; verify by throttling network in devtools or
      reloading mid-conversation.
- [ ] **Step 2:** Commit: `feat(ui): stage4: skeleton loaders + streaming caret`

### Task 4.6: Stage gate

- [ ] `pnpm -r type-check && pnpm --filter web lint && pnpm --filter web test`
- [ ] Chat-touching E2E ONLY (the full 26-spec dir runs serial and
      includes admin/skills surfaces Stage 5 is about to rewrite — full
      sweep belongs to Stage 7). From `frontend/`:

```bash
pnpm exec playwright test \
  packages/web/__tests__/e2e/chat-flow.spec.ts \
  packages/web/__tests__/e2e/streaming.spec.ts \
  packages/web/__tests__/e2e/steering.spec.ts \
  packages/web/__tests__/e2e/attachments.spec.ts \
  packages/web/__tests__/e2e/i18n.spec.ts \
  packages/web/__tests__/e2e/memory-reflection.spec.ts \
  packages/web/__tests__/e2e/workspace-switch.spec.ts \
  packages/web/__tests__/e2e/widget-shell.spec.ts
```

      Fix fallout now, not in Stage 7.
- [ ] `node scripts/dev/capture-screens.mjs 4-chat`
- [ ] `/code-review` (high); fix; commit.

---

## Stage 5: Management pages (workspace + admin)

### Task 5.1: Shared management modules

**Files:**
- Create: `frontend/packages/web/components/management/PageHeader.tsx`
- Create: `frontend/packages/web/components/management/ToolbarRow.tsx`
- Create: `frontend/packages/web/components/management/DangerZone.tsx`
- (MasterDetail stays a layout convention — two flex children — not a
  component; pages keep their own list/detail wiring.)

PageHeader:

```tsx
import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  description?: string
  /** at most one primary action, right-aligned */
  action?: ReactNode
}

export function PageHeader({ title, description, action }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4 px-6 pt-5 pb-4 border-b border-border">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}
```

ToolbarRow:

```tsx
import type { ReactNode } from 'react'

interface ToolbarRowProps {
  /** search input element */
  search?: ReactNode
  /** segmented filters (ui/tabs) */
  filters?: ReactNode
  /** trailing extras */
  children?: ReactNode
}

export function ToolbarRow({ search, filters, children }: ToolbarRowProps) {
  return (
    <div className="flex items-center gap-3 px-6 py-3 border-b border-border">
      {search && <div className="flex-1 max-w-md">{search}</div>}
      {filters}
      {children && <div className="ml-auto flex items-center gap-2">{children}</div>}
    </div>
  )
}
```

DangerZone:

```tsx
import type { ReactNode } from 'react'

export function DangerZone({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border border-danger-border rounded-lg overflow-hidden">
      <h2 className="px-4 py-2.5 text-sm font-medium text-danger-fg bg-danger-surface border-b border-danger-border">
        {title}
      </h2>
      <div className="p-4">{children}</div>
    </section>
  )
}
```

- [ ] **Step 1:** Create the three modules + a small render test each
      (`__tests__/components/management/*.test.tsx`: renders
      title/description/action; DangerZone renders children).
- [ ] **Step 2:** Tests pass; commit:
      `feat(ui): stage5: shared management modules (PageHeader/ToolbarRow/DangerZone)`

**Hard rule:** pages assemble these modules in their own files. NO
`ManagementPage` wrapper, NO `mode`/`scope` props — admin and workspace
pages stay separate (CLAUDE.md scope isolation).

### Task 5.2: Apply to workspace management pages (9 pages)

**Files (modify each page + its top-level components):**
`app/(app)/w/[wsId]/{memory,sandbox,sandbox-env,scheduled-tasks,settings,skills,triggers,triggers/[id]}/page.tsx`,
`app/(app)/workspaces/page.tsx`, plus their feature components
(`components/workspace-settings/**`, `components/skills/**`,
`components/triggers/**`, `components/sandbox-env/**`,
`components/workspace/**`).

Per page checklist (apply uniformly):
- [ ] Header → `PageHeader` (title 20px/600 + one-line description +
      single primary `Button`).
- [ ] Search/filter row → `ToolbarRow` with restyled `ui/tabs` as the
      segmented control (no new widget). Explicit migration list — fold
      the three existing hand-rolled toolbars into `ToolbarRow` (or the
      fragmentation survives): `components/mcp/MCPToolbar.tsx`,
      `components/admin/models/ModelsToolbar.tsx`,
      `components/workspace-settings/skills/WorkspaceSkillsToolbar.tsx`.
- [ ] Master-detail lists: selected item gets the same 2px left
      `before:bg-primary` indicator as the sidebar; detail empty state →
      `components/shared/EmptyState.tsx` (restyle it once: drop dashed
      border + `bg-muted/20`, use `border border-border rounded-lg
      bg-card`).
- [ ] Hardcoded colors → status token sets
      (`grep -n "amber\|emerald\|blue-\|green-\|red-" <file>` per file;
      MCPConnectorList disabled badge → warning set, skills "removed"
      label → danger set, etc.).
- [ ] Tables (`WsMembersTable` etc.): rows `h-11`, `hover:bg-accent`,
      actions `opacity-0 group-hover:opacity-100 focus-within:opacity-100
      transition-opacity duration-fast`.
- [ ] Destructive sections → `DangerZone`.
- [ ] Loading: skeleton rows matching layout; error: danger-surface bar
      with retry (these pages DO have refetch — wire to the existing
      fetch hook).
- [ ] Recoverable deletes (sandbox-env vars, triggers, scheduled tasks,
      memory items) → `useUndoableDelete`. **PREREQUISITE per flow — the
      current delete paths are API-first and cannot host an undo window
      as-is** (e.g. `triggerStore.remove` calls the API then filters;
      sandbox-env `handleDelete` is confirm→API→full `load()` refetch).
      For each flow, first add a `pendingDeleteIds: Set<string>` to the
      owning component/store with: `hide(id)` (add to set — list
      rendering filters it out), `restore(id)` (remove from set), and
      rendering that filters by the set so a concurrent refetch can NOT
      resurrect the row mid-window. Then wire:
      `hide(id); requestDelete(id, () => api.delete(id), { label:
      t('common.deleted'), actionLabel: t('common.undo'), onUndo: () =>
      restore(id) })`. Never call the existing API-first remove() inside
      requestDelete's commit only AFTER the window — the API call must
      not happen before commit fires, or Undo is a lie. Dangerous
      deletes (workspace itself) keep AlertDialog + type-the-name.
- [ ] `bg-accent` selected-state sweep for the files this stage touches
      (`WorkspaceSkillDetail.tsx` selected file rows are a known case) →
      `bg-raised` + 2px primary indicator.
- [ ] Replace existing hand-rolled `animate-pulse` skeleton blocks with
      `ui/skeleton.tsx` as pages are visited (known: admin
      `SkillsList.tsx`, `memory/components/MemoryList.tsx`,
      `ScheduledTasksList.tsx` — grep `animate-pulse` per page).
- [ ] Remove cleaned files from the Task 1.4 eslint allowlist as each
      page lands.

- [ ] **Step N (last):** Commit per feature-area batch, NOT per page
      (each commit costs ~30s of whole-workspace lint hooks): workspace
      pages in 2-3 commits (settings+members / skills+memory /
      triggers+scheduled+sandbox-env),
      `feat(ui): stage5: <area> on management modules`

### Task 5.3: Apply to admin pages (15 pages) + admin top bar

**Files:** `app/admin/**` pages + `components/admin/**`. Top bar:
`components/admin/AdminTopBar.tsx`, `components/admin/AdminAvatarMenu.tsx`,
`components/admin/AdminSubNav.tsx`.

- [ ] **Step 1:** AdminTopBar restyle: left = mark (`cx` placeholder,
      same as home) + `ADMIN` label (`text-xs uppercase tracking-wider
      text-faint font-medium`) + `/` separator (`text-faint`) + org name
      (`text-base font-medium`); right = "Back to app" ghost button
      (`border border-border-strong rounded hover:border-primary
      hover:text-primary transition-colors duration-fast`, ← icon) +
      avatar via the SAME account component as the sidebar footer
      (extract `components/sidebar/AvatarPopover.tsx` usage — reuse the
      component, do not fork; if admin needs different menu items, pass
      them as props).
- [ ] **Step 2:** AdminSubNav → sidebar visual language (item height,
      active 2px indicator, group labels) — same classes as Task 4.1.
- [ ] **Step 3:** Each of the 15 pages: same per-page checklist as Task
      5.2. Modal→sheet conversions where form > 3 fields:
      `ProviderFormDialog`, `UploadSkillModal`,
      `UploadWorkspaceSkillModal`, `MCPCustomCreatePanel` (if dialog),
      `EnvModal` → `Sheet` from `ui/sheet.tsx` (the Models add wizard
      stays full-page). ≤3-field dialogs (`AddOrgMemberDialog`,
      `AddWsMemberDialog`) stay dialogs.
- [ ] **Step 4:** Commit per feature-area cluster (3-4 commits for the
      15 admin pages: top bar+nav / models+presets+settings /
      skills+registries+mcp / sandbox+env+members+misc), not per page.
      Remove cleaned files from the Task 1.4 eslint allowlist as
      clusters land.

### Task 5.4: Stage gate

- [ ] `pnpm -r type-check && pnpm --filter web lint && pnpm --filter web test`
- [ ] E2E: management specs, from `frontend/`:
      `ls packages/web/__tests__/e2e/ | grep -E "admin|settings|trigger|scheduled|skills"`
      → `pnpm exec playwright test <those files, packages/web/-prefixed>`
- [ ] `node scripts/dev/capture-screens.mjs 5-management`
- [ ] `/code-review` (high — biggest diff); fix; commit.

---

## Stage 6: Mobile (chat only)

### Task 6.1: Sidebar drawer below `md`

**Files:**
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`
- Modify: `frontend/packages/web/app/(app)/layout.tsx` (hamburger in a
  mobile-only top strip)

- [ ] **Step 1:** Wrap sidebar: `hidden md:flex` for desktop; mobile
      rendering via `Sheet` side="left" (reuse Stage 2 primitive),
      triggered by a hamburger button visible `md:hidden` in a slim top
      bar (`h-11 border-b border-border flex items-center px-3`).
      Conversation click closes the drawer.
- [ ] **Step 2:** Desktop regression (from `frontend/`):
      `pnpm exec playwright test packages/web/__tests__/e2e/chat-flow.spec.ts`
      still green, then manual viewport 390×844 in the running
      app: drawer opens/closes, overlay dims.
- [ ] **Step 3:** Commit: `feat(ui): stage6: sidebar drawer on mobile`

### Task 6.2: Stream, input bar, right panel on mobile

**Files:**
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
  (`max-w-[760px]` → `max-w-full px-4 md:max-w-[760px] md:px-6`; user
  bubble `max-w-[88%] md:max-w-[78%]`)
- Modify: `frontend/packages/web/components/layout/InputBar.tsx`
  (container `pb-[env(safe-area-inset-bottom)]`; below `md`, hide the
  inline PresetPicker/ThinkingControl/attach controls and render a `+`
  `DropdownMenu` that RE-HOSTS the same components as menu content —
  import the same components, no logic duplication. `ThinkingBadge`
  stays visible OUTSIDE the `+` menu on mobile — it is the user's only
  indicator that elevated thinking is active)
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`
  (below `md`: right panel renders as a full-screen overlay —
  `fixed inset-0 z-50 bg-background flex flex-col` with slide-up
  animation `animate-in slide-in-from-bottom duration-slow` applied on
  mount (the overlay conditionally mounts; no data-state attr needed) —
  instead of a ResizablePanel; gate by a
  `useMediaQuery('(min-width: 768px)')` hook, create
  `hooks/useMediaQuery.ts` (hooks dir, NOT lib/): standard `matchMedia`
  + `useSyncExternalStore`)
- [ ] **Step 1:** Implement each; manual viewport checks (390×844): no
      horizontal scroll, input above keyboard, panel overlay full-screen
      with working close.
- [ ] **Step 2:** Desktop E2E re-run (chat specs) — green.
- [ ] **Step 3:** Commit: `feat(ui): stage6: mobile chat layout (stream,
      input +menu, panel overlay)`

### Task 6.3: Management narrow-viewport audit

**Files:** grid pseudo-tables only — confirmed offender:
`app/admin/sandbox/_components/CommandRulesTable.tsx` (and any sibling
found by `grep -rln "overflow-hidden" app/admin app/\(app\)/w --include='*.tsx' | xargs grep -ln "grid-cols-"`)

- [ ] **Step 1:** For each: outer wrapper gains `overflow-x-auto`; the
      grid gets an explicit `min-w-[560px]` INSIDE the scroll container
      (never inside `overflow-hidden`). `ui/table.tsx` usages already
      scroll — skip.
- [ ] **Step 2:** Manual 390px check on admin sandbox page: rules table
      scrolls horizontally, Remove button reachable.
- [ ] **Step 3:** Commit: `fix(ui): stage6: narrow-viewport overflow for
      grid pseudo-tables`

### Task 6.4: Stage gate

- [ ] `pnpm -r type-check && pnpm --filter web lint && pnpm --filter web test`
- [ ] `node scripts/dev/capture-screens.mjs 6-mobile`
- [ ] `/code-review` (medium); fix; commit.

---

## Stage 7: Motion polish, color sweep enforcement, delivery

### Task 7.1: Motion application pass

**Files:**
- Modify: `frontend/packages/web/app/globals.css` (keyframes)
- Modify: `frontend/packages/web/components/chat/MessageList.tsx` (new
  message entry), `Sidebar.tsx` + `PromptCards.tsx` (stagger),
  `ToolCallItem.tsx` (check scale-in), dialogs via `ui/alert-dialog.tsx`

- [ ] **Step 1:** Keyframes in globals.css:

```css
@keyframes rise-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes scale-in {
  from { opacity: 0; transform: scale(0.6); }
  to { opacity: 1; transform: scale(1); }
}
@utility animate-rise-in {
  animation: rise-in var(--transition-duration-base) var(--ease-out-quart) both;
}
@utility animate-scale-in {
  animation: scale-in var(--transition-duration-fast) var(--ease-out-quart) both;
}
```

- [ ] **Step 2:** Apply: new (streamed-in) message wrapper gets
      `animate-rise-in` ONLY when appended live (gate on a
      `isNew`/streaming flag already present in message store — never on
      history render). Sidebar conversation rows + prompt cards: stagger
      via inline `style={{ animationDelay: `${i * 30}ms` }}` capped at
      10 items, applied only on first mount (a `useRef(true)` mounted
      flag at list level). Tool check icon: `animate-scale-in` keyed on
      state transition. Dialog content (base-ui emits `data-open`, NOT
      Radix's `data-state=open`): `data-open:animate-in data-open:fade-in
      data-open:zoom-in-[0.96] duration-base`.
- [ ] **Step 3:** Verify `prefers-reduced-motion` kills all of it
      (devtools emulation). Commit:
      `feat(ui): stage7: motion application pass`

### Task 7.2: Hardcoded-color final sweep + enforcement

**Files:**
- Modify: `frontend/packages/web/components/chat/widget/WidgetView.tsx`
- Modify: `frontend/packages/web/eslint.config.mjs`
- Sweep: whatever the grep finds

- [ ] **Step 1:** Sweep:

```bash
grep -rEn "(bg|text|border|ring|divide|from|to)-(amber|blue|green|red|emerald|sky|yellow|purple|pink|orange|indigo|violet|teal|cyan|lime|rose|fuchsia|slate|gray|zinc|neutral|stone)-[0-9]" \
  frontend/packages/web/components frontend/packages/web/app --include='*.tsx' \
  | grep -v "components/chat/widget/"
```

      Fix every hit with the appropriate token class. Expected end state:
      zero hits outside `chat/widget/`.
- [ ] **Step 2:** WidgetView carve-out: derive the iframe palette from
      token values at serialization time — read computed styles once:

```ts
// chat/widget/widgetTheme.ts (new)
export function getWidgetPalette(isDark: boolean) {
  const styles = getComputedStyle(document.documentElement)
  const v = (name: string) => styles.getPropertyValue(name).trim()
  return {
    bg: v('--color-sunken') || (isDark ? '#050505' : '#fafafa'),
    fg: v('--color-foreground') || (isDark ? '#ededed' : '#171717'),
    muted: v('--color-raised') || (isDark ? '#111111' : '#f5f5f5'),
    border: v('--color-border') || (isDark ? '#1f1f1f' : '#eaeaea'),
    accent: v('--color-primary') || '#0070f3',
  }
}
```

      `WidgetView.tsx` uses this instead of its literal palettes (keep
      literals only as the SSR-safe fallbacks above). Run (from
      `frontend/`)
      `pnpm exec playwright test packages/web/__tests__/e2e/widget-shell.spec.ts`
      and update its fixture expectations to the token-derived values.
- [ ] **Step 3:** The eslint guard already exists (Task 1.4). Here:
      EMPTY its temporary allowlist (only the structural
      `components/chat/widget/**` ignore remains), then
      `pnpm --filter web lint` — must be green with zero allowlisted
      files. Negative test: temporarily add `bg-amber-500` in a plain
      string AND in a template literal — both must fail; revert.
- [ ] **Step 4:** Commit: `feat(ui): stage7: color sweep + eslint
      enforcement + widget palette from tokens`

### Task 7.3: Full verification sweep

- [ ] `pnpm -r type-check && pnpm -r lint && pnpm --filter web test`
- [ ] Full E2E from `frontend/`: `pnpm exec playwright test` (config
      loads worktree ports/baseURL; backend running on 8001)
- [ ] (No backend test run here — the branch has zero backend diff and
      the pre-push hook + CI both run the backend suite anyway; a third
      manual run is pure duplication)
- [ ] `node scripts/dev/capture-screens.mjs 7-final` — review both
      themes against `.superpowers/screens/0-baseline/`
- [ ] `/code-review` (max effort, whole-branch diff vs origin/main);
      fix; commit.

### Task 7.4: Delivery

- [ ] **Step 1:** Use `superpowers:verification-before-completion` —
      paste evidence of the full green suite.
- [ ] **Step 2:** Push branch; open ONE PR titled
      `feat(ui): full-product redesign — Vercel Mono direction (spec 2026-06-10)`
      with: summary per stage, the spec link, before/after screenshots
      (from `.superpowers/screens/`), and the E2E evidence.
- [ ] **Step 3:** Run the `pr-codex-review-loop` skill until clean
      (👍 reaction = clean; reply to every comment).
- [ ] **Step 4:** CI must pass (fix even pre-existing failures per
      project workflow); then merge and clean up the worktree via
      `superpowers:finishing-a-development-branch`.

---

## Self-review notes (already applied)

- Spec coverage: §1→Stage 1+2.1(type scale); §2→Stages 3+4; §3→Stage 5;
  §4→Stages 6+7.1; strategy §5/6→Stages 4.3 (selectors) + 1.4/7.2
  (enforcement: guard lands Stage 1 with shrinking allowlist, emptied in
  7.2 + widget carve-out). Undo toast → 2.3 + 5.2 (with per-flow
  pendingDeleteIds prerequisite). Theme migration → 1.1 + 1.3.
- Plan-review fixes incorporated (2026-06-10 /code-review round):
  motion namespace `--transition-duration-*` + tw-animate-css dependency
  (1.2); destructive→danger token mapping (1.2); type scale preserves
  16px text-base for iOS anti-zoom (2.1); sheet built on base-ui
  `data-open` not Radix (2.2); useUndoableDelete flushes on unmount +
  translated labels (2.3); command forms `pnpm -r type-check` + playwright
  from `frontend/` (all gates); real paths for ArtifactPanel /
  AssistantMessage memo alias / empty-state home / 6 placeholder specs
  (3.2, 4.2-4.4); composer-draft bridge resolves the PromptCards fork
  (4.4); accent selected-state sweep (1.2 note, 4.2, 5.2); rounded-full
  scoped to badge.tsx only (2.1); feature-area commit batching (5.2/5.3);
  chat-scoped 4.6 gate; no backend test duplication (7.3).
- Type consistency: `PanelHeaderProps.source` union used in 3.1/3.2;
  `useUndoableDelete(id, commit, { label, actionLabel, onUndo })`
  consistent between 2.3 and 5.2; `--color-*` /
  `--transition-duration-*` names consistent between 1.2 and all later
  class names/var() references.
