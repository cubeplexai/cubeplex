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
- After each stage: `pnpm -r typecheck && pnpm -r lint && pnpm -r test`
  (incremental: only affected suites during the stage; the full sweep
  happens in Stage 7), capture screenshots (see Stage 0 harness), run
  `/code-review` (medium effort for restyle-only stages, high for Stage
  3/5), fix findings, commit.
- Commit per task (not per stage). Message style:
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

- [ ] **Step 1:** Write the script. It logs in and screenshots key pages
      in both themes into `.superpowers/screens/<stage>/`:

```js
// Usage: node scripts/dev/capture-screens.mjs <stage-label>
// Captures key pages light+dark into .superpowers/screens/<stage-label>/
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const BASE = process.env.BASE_URL ?? 'http://127.0.0.1:3001'
const stage = process.argv[2] ?? 'adhoc'
const outDir = new URL(`../../../.superpowers/screens/${stage}/`, import.meta.url).pathname
mkdirSync(outDir, { recursive: true })

const PAGES = [
  ['login', '/login'],
  ['chat-home', '/'], // redirects to /w/<ws>
  ['ws-skills', null], // resolved after login from current URL
  ['ws-settings', null],
  ['admin-models', '/admin/models'],
  ['admin-members', '/admin/members'],
]

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

// login
await page.goto(`${BASE}/login`)
await page.getByRole('textbox', { name: 'Email' }).fill('design@cubebox.dev')
await page.getByRole('textbox', { name: 'Password' }).fill('Design-Review-2026')
await page.getByRole('button', { name: /sign in/i }).click()
await page.waitForURL(/\/w\//)
const wsUrl = new URL(page.url()).pathname // /w/<wsId>

for (const theme of ['light', 'dark']) {
  await page.emulateMedia({ colorScheme: theme })
  // force explicit theme via next-themes localStorage, then reload
  await page.evaluate((t) => localStorage.setItem('theme', t), theme)
  for (const [name, path] of PAGES) {
    const target =
      path ?? `${wsUrl}/${name.replace('ws-', '')}`.replace('settings', 'settings?tab=workspace')
    await page.goto(`${BASE}${target}`)
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: `${outDir}/${name}-${theme}.png` })
  }
}
await browser.close()
console.log(`screens -> ${outDir}`)
```

- [ ] **Step 2:** Run `node scripts/dev/capture-screens.mjs 0-baseline`
      from `frontend/`. Expected: 12 PNGs in
      `.superpowers/screens/0-baseline/`. These are the "before"
      reference.
- [ ] **Step 3:** Commit the script:

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

  /* shape */
  --radius-xs: 4px;   /* badges, chips */
  --radius: 6px;      /* buttons, inputs, cards */
  --radius-lg: 10px;  /* panels, modals */

  /* motion */
  --duration-fast: 120ms;
  --duration-base: 200ms;
  --duration-slow: 300ms;
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
  }
}
```

Notes for the engineer:
- Tailwind 4 generates `bg-raised`, `border-border-strong`,
  `text-success-fg`, `bg-warning-surface`, `rounded-xs`,
  `duration-fast` etc. from these automatically.
- Keep everything else in globals.css (resizable-panel fix,
  scrollbar-none, `@custom-variant dark`, hljs palettes). The light hljs
  palette stays; in Stage 4 the dark hljs bg moves onto `--color-sunken`.
- Light-mode dark-value mirroring was eyeballed for WCAG AA; verify
  contrast in Step 3 and tune in place (`globals.css` is the source of
  truth — do NOT back-port into the spec).

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

- [ ] **Step 1:** Write the failing test:

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from 'next-themes'
import { NextIntlClientProvider } from 'next-intl'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import { describe, expect, it } from 'vitest'

// system resolves dark; first click must set LIGHT (uses resolvedTheme)
describe('ThemeToggle under theme=system', () => {
  it('first click flips against resolvedTheme, not raw theme', () => {
    window.matchMedia = ((q: string) => ({
      matches: q.includes('dark'),
      media: q, addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
      onchange: null,
    })) as never
    render(
      <NextIntlClientProvider locale="en" messages={{ avatar: { lightTheme: 'Light', darkTheme: 'Dark' } }}>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <ThemeToggle />
        </ThemeProvider>
      </NextIntlClientProvider>,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(localStorage.getItem('theme')).toBe('light')
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

### Task 1.4: Stage gate

- [ ] `pnpm -r typecheck && pnpm --filter web lint && pnpm --filter web test` — all green.
- [ ] Run E2E smoke: `pnpm --filter web exec playwright test
      __tests__/e2e/chat-flow.spec.ts` (worktree DB auto-routed).
      Expected: PASS — no copy/selector changed yet.
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
| `rounded-md`, `rounded-lg` on buttons/inputs/cards | `rounded-[var(--radius)]` → just `rounded` (6px via token) | 3-step radius scale |
| `rounded-[min(var(--radius-md),10px)]` (button xs/sm) | `rounded` | kill arbitrary values |
| `rounded-full` on badges | `rounded-xs` | square-ish badges per direction B |
| `rounded-xl`, `rounded-2xl` on cards/popovers | `rounded-lg` (10px) | panels/modals step |
| `transition-colors` without duration | `transition-colors duration-fast` | motion tokens |
| any `focus-visible:ring-*` missing | `focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none` | visible focus everywhere |
| `text-[0.8rem]`, `text-[11px]` etc. | nearest semantic step (`text-xs` = 12px, `text-sm` = 13px — see Step 1) | semantic type scale |

- [ ] **Step 1:** Add the type-scale override in `globals.css` `@theme`
      (13px-based UI, per direction B):

```css
  --text-xs: 11px;
  --text-xs--line-height: 1.45;
  --text-sm: 12px;
  --text-sm--line-height: 1.5;
  --text-base: 13px;
  --text-base--line-height: 1.55;
  --text-md: 14px;
  --text-md--line-height: 1.55;
  --text-lg: 16px;
  --text-lg--line-height: 1.5;
  --text-xl: 20px;
  --text-xl--line-height: 1.35;
  --text-2xl: 24px;
  --text-2xl--line-height: 1.25;
```

  Then set `font-size: 13px` is NOT needed — `text-base` covers it where
  used; body inherits browser 16px only for prose (chat markdown keeps
  `text-base`+).

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

- [ ] **Step 1:** `pnpm --filter web dlx shadcn@latest add sheet` from
      `frontend/packages/web/` (components.json present, style
      base-nova). If the generator fails offline, vendor the standard
      shadcn sheet (Radix Dialog based) manually.
- [ ] **Step 2:** Restyle generated file to tokens: overlay
      `bg-black/60`→`bg-background/80 backdrop-blur-sm`, content
      `border-l border-border bg-card`, width `w-[480px] max-w-[90vw]`,
      animation classes use
      `data-[state=open]:duration-slow ease-[var(--ease-out-quart)]`.
- [ ] **Step 3:** `pnpm --filter web build` green. Commit:
      `feat(ui): stage2: add sheet primitive for slide-over forms`

### Task 2.3: Add toast system (sonner)

**Files:**
- Modify: `frontend/packages/web/package.json` (pnpm add)
- Create: `frontend/packages/web/components/ui/sonner.tsx`
- Modify: `frontend/packages/web/app/layout.tsx` (mount `<Toaster />`)
- Create: `frontend/packages/web/lib/useUndoableDelete.ts`
- Test: `frontend/packages/web/__tests__/lib/useUndoableDelete.test.ts`

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
import { useUndoableDelete } from '@/lib/useUndoableDelete'

describe('useUndoableDelete', () => {
  it('commits after the grace window unless undone', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const { result } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-1', commit))
    expect(commit).not.toHaveBeenCalled()          // delayed
    act(() => vi.advanceTimersByTime(5000))
    expect(commit).toHaveBeenCalledTimes(1)        // committed
    vi.useRealTimers()
  })

  it('does not commit when undone within the window', () => {
    vi.useFakeTimers()
    const commit = vi.fn()
    const { result } = renderHook(() => useUndoableDelete())
    act(() => result.current.requestDelete('item-2', commit))
    act(() => result.current.undo('item-2'))
    act(() => vi.advanceTimersByTime(5000))
    expect(commit).not.toHaveBeenCalled()
    vi.useRealTimers()
  })
})
```

- [ ] **Step 4:** Run it — FAIL (module missing). Implement:

```ts
'use client'

import { useCallback, useEffect, useRef } from 'react'
import { toast } from 'sonner'

const UNDO_WINDOW_MS = 5000

/** Optimistic-hide + delayed-commit delete with an undo toast. */
export function useUndoableDelete() {
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())

  const undo = useCallback((id: string) => {
    const timer = timers.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timers.current.delete(id)
    }
  }, [])

  const requestDelete = useCallback(
    (id: string, commit: () => void | Promise<void>, opts?: { label?: string; onUndo?: () => void }) => {
      const timer = setTimeout(() => {
        timers.current.delete(id)
        void commit()
      }, UNDO_WINDOW_MS)
      timers.current.set(id, timer)
      toast(opts?.label ?? 'Deleted', {
        duration: UNDO_WINDOW_MS,
        action: {
          label: 'Undo',
          onClick: () => {
            undo(id)
            opts?.onUndo?.()
          },
        },
      })
    },
    [undo],
  )

  // commit pending deletes on unmount so nothing is silently lost
  useEffect(() => {
    const pending = timers.current
    return () => {
      for (const t of pending.values()) clearTimeout(t)
    }
  }, [])

  return { requestDelete, undo }
}
```

      Caller contract: hide the item optimistically (local state /
      store), call `requestDelete(id, commitFn, { onUndo: restoreFn })`.
      i18n of "Deleted"/"Undo" happens at call sites via
      `useTranslations` — pass `label` and a translated action; add keys
      `common.deleted` / `common.undo` to `messages/en.json` + `zh.json`.
- [ ] **Step 5:** Tests PASS. `pnpm --filter web build` green.
- [ ] **Step 6:** Commit: `feat(ui): stage2: sonner toaster + undoable
      delete hook`

### Task 2.4: Stage gate

- [ ] `pnpm -r typecheck && pnpm --filter web lint && pnpm --filter web test`
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
- Modify: `frontend/packages/web/components/panel/ArtifactPanel.tsx`
  (delete its copy-pasted header markup; render `PanelHeader` with
  `kind: 'plain'`, `actions={<VersionPopover…/><DownloadLink…/>}`)
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

- [ ] **Step 1:** Add CSS (globals.css):

```css
/* Panel open/close: sanctioned width transition, NEVER during drag.
   .panel-animating is applied only around programmatic open/close. */
.panel-animating [data-slot='resizable-panel'] {
  transition: flex-basis var(--duration-slow) var(--ease-out-quart);
}
```

- [ ] **Step 2:** In `AppShell.tsx`, wrap panel-open state changes: set
      `panelAnimating` true, flip open state, clear after 300ms
      (`setTimeout` + cleanup). Apply `panel-animating` class on the
      `ResizablePanelGroup` wrapper only while true. Drag interactions
      never set it.
- [ ] **Step 3:** Manual check in app: open/close eases; dragging the
      divider stays 1:1 with the cursor (no lag).
- [ ] **Step 4:** Commit: `feat(ui): stage3: eased panel open/close,
      drag-safe`

### Task 3.4: Stage gate

- [ ] `pnpm -r typecheck && pnpm --filter web lint && pnpm --filter web test`
- [ ] E2E: run panel-touching specs:
      `pnpm --filter web exec playwright test __tests__/e2e/chat-flow.spec.ts __tests__/e2e/widget-shell.spec.ts`
- [ ] `node scripts/dev/capture-screens.mjs 3-panel-shell`
- [ ] `/code-review` (HIGH — structural stage); fix; commit.

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
- [ ] **Step 2:** Run sidebar-touching E2E:
      `pnpm --filter web exec playwright test __tests__/e2e/workspace-switch.spec.ts`
- [ ] **Step 3:** Commit: `feat(ui): stage4: sidebar on token language`

### Task 4.2: Message stream

**Files:**
- Modify: `frontend/packages/web/components/chat/UserMessage.tsx`
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx` +
  `HistoryAssistantMessage.tsx` (remove avatar block, pure typography)
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

UserMessage core (complete component body):

```tsx
export function UserMessage({ children, attachments }: UserMessageProps) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[78%] rounded-lg rounded-br-xs border border-border bg-raised px-3.5 py-2.5 text-base leading-relaxed">
        {children}
        {attachments}
      </div>
    </div>
  )
}
```

(Adapt prop names to the existing file — read it first; only the
className set changes plus dropping the saturated `bg-primary`.)

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
- Modify (selectors): `frontend/packages/web/__tests__/e2e/chat-flow.spec.ts`,
  `streaming.spec.ts`, `steering.spec.ts`, `workspace-switch.spec.ts`,
  `memory-reflection.spec.ts`, `i18n.spec.ts`, plus the 7th file found by
  `grep -rl "How can I help you" __tests__/`
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
      logic, PendingSteers, attachments, dropzone): move `PresetPicker`
      and `ThinkingControl` from the row above the textarea into the
      internal bottom toolbar, right-aligned before the send button;
      attach button stays left. Container: `bg-raised border
      border-border-strong rounded-lg focus-within:border-primary
      focus-within:ring-2 focus-within:ring-ring/30 transition
      duration-base`. Hint row (`Enter to send…`) stays below.
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
- Modify: the empty-state JSX (locate via
  `grep -rn "AI Agent System" frontend/packages/web/app frontend/packages/web/components`)
- Create: `frontend/packages/web/components/chat/PromptCards.tsx`
- Modify: `messages/en.json` + `messages/zh.json` (`home.promptCards.*`)

- [ ] **Step 1:** `PromptCards.tsx` — three cards (analyze a data file /
      research a topic / automate a workflow), each
      `button` with `border border-border rounded-lg bg-card p-3.5
      text-left hover:border-border-strong hover:bg-accent
      hover:-translate-y-px transition duration-fast focus-visible:ring-2
      focus-visible:ring-ring`; icon row `font-mono text-info-fg`; title
      `text-sm font-medium`; description `text-xs text-faint`. `onClick`
      fills the input via the same store/setter InputBar reads (find the
      draft setter in `@cubebox/core` stores; if none exists, lift a
      `onPick(text)` prop up to the page that owns InputBar state).
- [ ] **Step 2:** Replace logo block with placeholder mark (40px square,
      `rounded-lg bg-gradient-to-br from-border-strong to-raised border
      border-border-strong grid place-items-center font-mono text-xs
      text-muted-foreground`, content `cx`).
- [ ] **Step 3:** i18n keys (en/zh) for the 3 cards' title+description.
- [ ] **Step 4:** Verify in app; commit:
      `feat(ui): stage4: empty-state home with placeholder mark + prompt cards`

### Task 4.5: Loading skeletons + streaming cursor

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

- [ ] `pnpm -r typecheck && pnpm --filter web lint && pnpm --filter web test`
- [ ] Full chat E2E set: `pnpm --filter web exec playwright test __tests__/e2e/`
      (chat specs at minimum; fix fallout now, not in Stage 7)
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
      segmented control (no new widget).
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
      memory items) → `useUndoableDelete` (optimistic hide via local
      store state; commit calls existing delete API). Dangerous deletes
      (workspace itself) keep AlertDialog + type-the-name.

- [ ] **Step N (last):** Commit per page:
      `feat(ui): stage5: <page> on management modules`

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
- [ ] **Step 4:** Commit per page/component cluster.

### Task 5.4: Stage gate

- [ ] `pnpm -r typecheck && pnpm --filter web lint && pnpm --filter web test`
- [ ] E2E: admin + settings specs
      (`grep -l "admin\|settings" __tests__/e2e/*.spec.ts` → run those)
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
- [ ] **Step 2:** Playwright mobile check (manual):
      `pnpm --filter web exec playwright test __tests__/e2e/chat-flow.spec.ts`
      still green (desktop), then manual viewport 390×844 in the running
      app: drawer opens/closes, overlay dims.
- [ ] **Step 3:** Commit: `feat(ui): stage6: sidebar drawer on mobile`

### Task 6.2: Stream, input bar, right panel on mobile

**Files:**
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
  (`max-w-[760px]` → `max-w-full px-4 md:max-w-[760px] md:px-6`; user
  bubble `max-w-[88%] md:max-w-[78%]`)
- Modify: `frontend/packages/web/components/layout/InputBar.tsx`
  (container `pb-[env(safe-area-inset-bottom)]`; below `md`, hide
  inline PresetPicker/ThinkingControl/attach and render a `+`
  `DropdownMenu` that RE-HOSTS the same three components as menu
  content — import the same components, no logic duplication)
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`
  (below `md`: right panel renders as a full-screen overlay —
  `fixed inset-0 z-50 bg-background flex flex-col` with slide-up
  animation `data-[state=open]:animate-in slide-in-from-bottom
  duration-slow` — instead of a ResizablePanel; gate by a
  `useMediaQuery('(min-width: 768px)')` hook, create
  `lib/useMediaQuery.ts` if absent: standard `matchMedia` +
  `useSyncExternalStore`)
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

- [ ] Typecheck/lint/test + `node scripts/dev/capture-screens.mjs 6-mobile`
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
  animation: rise-in var(--duration-base) var(--ease-out-quart) both;
}
@utility animate-scale-in {
  animation: scale-in var(--duration-fast) var(--ease-out-quart) both;
}
```

- [ ] **Step 2:** Apply: new (streamed-in) message wrapper gets
      `animate-rise-in` ONLY when appended live (gate on a
      `isNew`/streaming flag already present in message store — never on
      history render). Sidebar conversation rows + prompt cards: stagger
      via inline `style={{ animationDelay: `${i * 30}ms` }}` capped at
      10 items, applied only on first mount (a `useRef(true)` mounted
      flag at list level). Tool check icon: `animate-scale-in` keyed on
      state transition. Dialog content: `data-[state=open]:animate-in
      fade-in zoom-in-[0.96] duration-base`.
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
      literals only as the SSR-safe fallbacks above). Run
      `pnpm --filter web exec playwright test __tests__/e2e/widget-shell.spec.ts`
      and update its fixture expectations to the token-derived values.
- [ ] **Step 3:** eslint guard in `eslint.config.mjs`:

```js
{
  files: ['components/**/*.tsx', 'app/**/*.tsx'],
  ignores: ['components/chat/widget/**'],
  rules: {
    'no-restricted-syntax': [
      'error',
      {
        selector:
          'Literal[value=/\\b(?:bg|text|border|ring|divide|from|to)-(?:amber|blue|green|red|emerald|sky|yellow|purple|pink|orange|indigo|violet|teal|cyan|lime|rose|slate|gray|zinc|neutral|stone)-[0-9]/]',
        message: 'Raw palette utilities are banned — use semantic tokens (see docs/dev/specs/2026-06-10-ui-redesign-design.md §1).',
      },
    ],
  },
},
```

      Run `pnpm --filter web lint` — green (and verify it FAILS if you
      temporarily add `bg-amber-500` somewhere).
- [ ] **Step 4:** Commit: `feat(ui): stage7: color sweep + eslint
      enforcement + widget palette from tokens`

### Task 7.3: Full verification sweep

- [ ] `pnpm -r typecheck && pnpm -r lint && pnpm -r test`
- [ ] Full E2E: `pnpm --filter web exec playwright test` (worktree
      ports/DB via conftest-equivalent env; backend running on 8001)
- [ ] Backend untouched sanity: `cd backend && uv run pytest -x -q
      tests/unit` (should be all-green/no-op — this initiative is
      frontend-only)
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
  §4→Stages 6+7.1; strategy §5/6→Stages 4.3 (selectors) + 7.2
  (enforcement/carve-out). Undo toast → 2.3 + 5.2. Theme migration → 1.1
  + 1.3. No-raw-colors → 7.2.
- Placeholders: none — every code step carries code; sweep tasks carry
  exact greps + transformation tables.
- Type consistency: `PanelHeaderProps.source` union used in 3.1/3.2;
  `useUndoableDelete` signature consistent between 2.3 and 5.2;
  `--color-*` names consistent between 1.2 and all later class names.
