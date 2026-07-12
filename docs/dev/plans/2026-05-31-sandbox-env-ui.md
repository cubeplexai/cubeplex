# Sandbox Env Vault UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two standalone pages — `/admin/sandbox-env` (org scope) and `/w/[wsId]/sandbox-env` (workspace + user scope) — for viewing, adding, rotating, and deleting sandbox environment variables.

**Architecture:** Shared `EnvTable` / `EnvModal` / `WarningCell` components live under the workspace page's `_components/` directory and are imported by both pages. The workspace page is role-aware: admins see both workspace-scope and user-scope entries; non-admins see only their own user-scope entries. All API calls go through `@cubeplex/core`.

**Tech Stack:** Next.js 15 App Router, React 19, TypeScript strict, `@cubeplex/core` API client, `useWorkspaceStore` (Zustand) for role, next-intl for i18n, shadcn/ui primitives, Tailwind CSS.

---

## File map

| File | Action | Purpose |
|---|---|---|
| `frontend/packages/core/src/api/sandboxEnv.ts` | Create | API client functions for all sandbox-env endpoints |
| `frontend/packages/core/src/api/index.ts` | Modify | Export `sandboxEnv` |
| `frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/page.tsx` | Create | Workspace env page (role-aware) |
| `frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/EnvTable.tsx` | Create | Shared table for both pages |
| `frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/EnvModal.tsx` | Create | Add / rotate modal |
| `frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/WarningCell.tsx` | Create | Warning icon + tooltip |
| `frontend/packages/web/app/admin/sandbox-env/page.tsx` | Create | Admin env page |
| `frontend/packages/web/components/admin/AdminSubNav.tsx` | Modify | Add `sandboxEnv` nav entry |
| `frontend/packages/web/components/layout/Sidebar.tsx` | Modify | Add `sandboxEnv` nav entry |
| `frontend/packages/web/messages/en.json` | Modify | Add i18n keys |
| `frontend/packages/web/messages/zh.json` | Modify | Add i18n keys |

---

## Task 1: Core API client (`@cubeplex/core`)

**Files:**
- Create: `frontend/packages/core/src/api/sandboxEnv.ts`
- Modify: `frontend/packages/core/src/api/index.ts`

- [ ] **Step 1.1: Create `sandboxEnv.ts` with types and API functions**

```typescript
// frontend/packages/core/src/api/sandboxEnv.ts
import { toApiError, type ApiClient } from './client'

export interface EnvEntryOut {
  id: string
  env_name: string
  is_secret: boolean
  scope: 'org' | 'workspace' | 'user'
  workspace_id: string | null
  user_id: string | null
  hosts: string[] | null
  status: string
  warnings: string[]
}

export interface EnvEntryListOut {
  entries: EnvEntryOut[]
}

export interface CreateEnvIn {
  env_name: string
  is_secret: boolean
  hosts?: string[] | null
  secret_value?: string | null
  plain_value?: string | null
}

export interface RotateSecretIn {
  secret_value: string
}

// ── Workspace: workspace scope (/workspace) ──────────────────────────────────

export async function listWsEnvWorkspace(
  client: ApiClient,
  wsId: string,
): Promise<EnvEntryListOut> {
  const res = await client.get(`/api/v1/ws/${wsId}/sandbox-env/workspace`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryListOut
}

export async function createWsEnvWorkspace(
  client: ApiClient,
  wsId: string,
  body: CreateEnvIn,
): Promise<EnvEntryOut> {
  const res = await client.post(`/api/v1/ws/${wsId}/sandbox-env/workspace`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function rotateWsEnvWorkspace(
  client: ApiClient,
  wsId: string,
  id: string,
  body: RotateSecretIn,
): Promise<EnvEntryOut> {
  const res = await client.patch(`/api/v1/ws/${wsId}/sandbox-env/workspace/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function deleteWsEnvWorkspace(
  client: ApiClient,
  wsId: string,
  id: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/sandbox-env/workspace/${id}`)
  if (!res.ok) throw await toApiError(res)
}

// ── Workspace: user scope (/me) ───────────────────────────────────────────────

export async function listWsEnvMe(client: ApiClient, wsId: string): Promise<EnvEntryListOut> {
  const res = await client.get(`/api/v1/ws/${wsId}/sandbox-env/me`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryListOut
}

export async function createWsEnvMe(
  client: ApiClient,
  wsId: string,
  body: CreateEnvIn,
): Promise<EnvEntryOut> {
  const res = await client.post(`/api/v1/ws/${wsId}/sandbox-env/me`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function rotateWsEnvMe(
  client: ApiClient,
  wsId: string,
  id: string,
  body: RotateSecretIn,
): Promise<EnvEntryOut> {
  const res = await client.patch(`/api/v1/ws/${wsId}/sandbox-env/me/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function deleteWsEnvMe(
  client: ApiClient,
  wsId: string,
  id: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/sandbox-env/me/${id}`)
  if (!res.ok) throw await toApiError(res)
}

// ── Admin: org scope ──────────────────────────────────────────────────────────

export async function listAdminEnv(client: ApiClient): Promise<EnvEntryListOut> {
  const res = await client.get('/api/v1/admin/sandbox-env')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryListOut
}

export async function createAdminEnv(client: ApiClient, body: CreateEnvIn): Promise<EnvEntryOut> {
  const res = await client.post('/api/v1/admin/sandbox-env', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function rotateAdminEnv(
  client: ApiClient,
  id: string,
  body: RotateSecretIn,
): Promise<EnvEntryOut> {
  const res = await client.patch(`/api/v1/admin/sandbox-env/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function deleteAdminEnv(client: ApiClient, id: string): Promise<void> {
  const res = await client.del(`/api/v1/admin/sandbox-env/${id}`)
  if (!res.ok) throw await toApiError(res)
}
```

- [ ] **Step 1.2: Export from core index**

In `frontend/packages/core/src/api/index.ts`, add after the `sandboxPolicy` export line:

```typescript
export * from './sandboxEnv'
```

- [ ] **Step 1.3: Build core package to verify types**

```bash
cd frontend/packages/core && pnpm build
```

Expected: no TypeScript errors, `dist/` updated.

- [ ] **Step 1.4: Commit**

```bash
cd frontend
git add packages/core/src/api/sandboxEnv.ts packages/core/src/api/index.ts
git commit -m "feat(sandbox-env-ui): add @cubeplex/core API client for sandbox env vault"
```

---

## Task 2: i18n keys

**Files:**
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 2.1: Add `sandboxEnv` to `adminNav` in `en.json`**

In `frontend/packages/web/messages/en.json`, in the `"adminNav"` object, add after `"sandbox"`:

```json
"sandboxEnv": "Sandbox env"
```

So the block looks like:
```json
"adminNav": {
  "settings": "Org Settings",
  "models": "Models",
  "webTools": "Web Tools",
  "skills": "Skills",
  "mcp": "MCP Connectors",
  "sandbox": "Sandbox policy",
  "sandboxEnv": "Sandbox env",
  "insights": "Insights",
  "members": "Members",
  "extensions": "Extensions"
},
```

- [ ] **Step 2.2: Add `sandboxEnv` to `sidebar` in `en.json`**

In the `"sidebar"` object, add after `"triggers"`:

```json
"sandboxEnv": "Sandbox env"
```

- [ ] **Step 2.3: Add matching keys to `zh.json`**

In `frontend/packages/web/messages/zh.json`:

In `"adminNav"`, add after `"sandbox"`:
```json
"sandboxEnv": "沙盒环境变量"
```

In `"sidebar"`, add after `"triggers"`:
```json
"sandboxEnv": "沙盒环境变量"
```

- [ ] **Step 2.4: Verify i18n parity check passes**

```bash
cd frontend
pnpm --filter web run lint
```

Expected: no i18n key parity errors.

- [ ] **Step 2.5: Commit**

```bash
git add packages/web/messages/en.json packages/web/messages/zh.json
git commit -m "feat(sandbox-env-ui): add i18n keys for sandbox env nav entries"
```

---

## Task 3: Navigation wiring

**Files:**
- Modify: `frontend/packages/web/components/admin/AdminSubNav.tsx`
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`

- [ ] **Step 3.1: Add `sandboxEnv` entry to `AdminSubNav`**

In `frontend/packages/web/components/admin/AdminSubNav.tsx`:

1. Add `KeyRound` to the lucide-react import:
```typescript
import { BarChart3, Box, Cpu, Globe, KeyRound, Plug, Puzzle, Settings, Sparkles, Users } from 'lucide-react'
```

2. In `NATIVE_ITEMS`, add after `{ href: '/admin/sandbox', ... }`:
```typescript
{ href: '/admin/sandbox-env', label: t('sandboxEnv'), icon: KeyRound },
```

- [ ] **Step 3.2: Add `sandboxEnv` entry to workspace `Sidebar`**

In `frontend/packages/web/components/layout/Sidebar.tsx`:

1. Add `KeyRound` to the lucide-react import (find the existing import line and add it).

2. Add `'sandboxEnv'` to the `labelKey` union type:
```typescript
labelKey: 'skills' | 'mcp' | 'memory' | 'scheduledTasks' | 'members' | 'settings' | 'triggers' | 'sandboxEnv'
```

3. In the `WorkspaceNavSection` component, add a prefix variable and active check alongside the others:
```typescript
const sandboxEnvPrefix = `/w/${wsId}/sandbox-env`
const onSandboxEnv = pathname?.startsWith(sandboxEnvPrefix) ?? false
```

4. Add the entry to the `entries` array, after `triggers`:
```typescript
{
  key: 'sandboxEnv',
  labelKey: 'sandboxEnv',
  icon: KeyRound,
  href: sandboxEnvPrefix,
  isActive: onSandboxEnv,
},
```

- [ ] **Step 3.3: TypeScript check**

```bash
cd frontend && pnpm --filter web exec tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3.4: Commit**

```bash
git add packages/web/components/admin/AdminSubNav.tsx packages/web/components/layout/Sidebar.tsx
git commit -m "feat(sandbox-env-ui): wire sandboxEnv nav entries in admin sidebar and workspace sidebar"
```

---

## Task 4: `WarningCell` component

**Files:**
- Create: `frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/WarningCell.tsx`

- [ ] **Step 4.1: Create `WarningCell`**

```typescript
// frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/WarningCell.tsx
'use client'

import { AlertTriangle } from 'lucide-react'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'

interface Props {
  warnings: string[]
}

export function WarningCell({ warnings }: Props) {
  if (warnings.length === 0) return null

  const label = `Blocked by network policy: ${warnings.join(', ')}`

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex items-center">
            <AlertTriangle className="size-3.5 text-amber-500" />
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs">
          {label}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
```

- [ ] **Step 4.2: TypeScript check**

```bash
cd frontend && pnpm --filter web exec tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4.3: Commit**

```bash
git add "frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/WarningCell.tsx"
git commit -m "feat(sandbox-env-ui): WarningCell component"
```

---

## Task 5: `EnvModal` component

**Files:**
- Create: `frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/EnvModal.tsx`

- [ ] **Step 5.1: Create `EnvModal`**

```typescript
// frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/EnvModal.tsx
'use client'

import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { type CreateEnvIn, type EnvEntryOut } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

export type ModalMode =
  | { kind: 'add-org' }
  | { kind: 'add-workspace'; defaultScope: 'workspace' | 'user' }
  | { kind: 'rotate'; entry: EnvEntryOut }

interface Props {
  mode: ModalMode
  onSubmit: (
    body: CreateEnvIn | { secret_value: string },
    entryId?: string,
    scope?: 'workspace' | 'user',
  ) => Promise<void>
  onClose: () => void
}

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

function parseHosts(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

export function EnvModal({ mode, onSubmit, onClose }: Props) {
  const isRotate = mode.kind === 'rotate'
  const isOrg = mode.kind === 'add-org'

  const [name, setName] = useState(isRotate ? mode.entry.env_name : '')
  const [scope, setScope] = useState<'workspace' | 'user'>(
    mode.kind === 'add-workspace' ? mode.defaultScope : 'workspace',
  )
  const [isSecret, setIsSecret] = useState(true)
  const [value, setValue] = useState('')
  const [hostsRaw, setHostsRaw] = useState('')
  const [nameError, setNameError] = useState<string | null>(null)
  const [hostsError, setHostsError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // Reset value when switching secret/plain
  useEffect(() => {
    setValue('')
  }, [isSecret])

  function validateName(v: string): string | null {
    if (!v) return 'Name is required'
    if (v.length > 128) return 'Max 128 characters'
    if (!NAME_RE.test(v)) return 'Use letters, digits, or underscores; must start with a letter or underscore'
    return null
  }

  function validateHosts(raw: string): string | null {
    if (!isSecret) return null
    const hosts = parseHosts(raw)
    if (hosts.length === 0) return 'At least one host is required for secrets'
    const invalid = hosts.filter((h) => !/^(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(h))
    if (invalid.length > 0) return `Invalid host pattern: ${invalid[0]}`
    return null
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitError(null)

    const nErr = isRotate ? null : validateName(name)
    const hErr = isSecret ? validateHosts(hostsRaw) : null
    setNameError(nErr)
    setHostsError(hErr)
    if (nErr || hErr) return
    if (!value) {
      setSubmitError('Value is required')
      return
    }

    setSaving(true)
    try {
      if (isRotate) {
        await onSubmit({ secret_value: value }, mode.entry.id)
      } else {
        const body: CreateEnvIn = {
          env_name: name,
          is_secret: isSecret,
          ...(isSecret
            ? { secret_value: value, hosts: parseHosts(hostsRaw) }
            : { plain_value: value }),
        }
        // Pass the final scope selection (only relevant for workspace-mode adds)
        const finalScope = mode.kind === 'add-workspace' ? scope : undefined
        await onSubmit(body, undefined, finalScope)
      }
      onClose()
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : 'An error occurred')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="relative w-full max-w-md rounded-xl border border-border/70 bg-background p-6 shadow-xl">
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-muted-foreground hover:text-foreground"
        >
          <X className="size-4" />
        </button>

        <h2 className="mb-5 text-base font-semibold">
          {isRotate ? 'Rotate secret' : 'Add environment variable'}
        </h2>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {/* NAME */}
          {!isRotate && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="env-name" className="text-xs font-medium">
                Name
              </Label>
              <Input
                id="env-name"
                value={name}
                onChange={(e) => setName(e.target.value.toUpperCase())}
                onBlur={() => setNameError(validateName(name))}
                className="font-mono text-sm"
                placeholder="VARIABLE_NAME"
                maxLength={128}
              />
              {nameError && <p className="text-xs text-destructive">{nameError}</p>}
            </div>
          )}

          {/* SCOPE — only for workspace-admin add */}
          {mode.kind === 'add-workspace' && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs font-medium">Scope</Label>
              <div className="flex gap-3">
                {(['workspace', 'user'] as const).map((s) => (
                  <label key={s} className="flex cursor-pointer items-center gap-1.5 text-sm">
                    <input
                      type="radio"
                      name="scope"
                      value={s}
                      checked={scope === s}
                      onChange={() => setScope(s)}
                    />
                    {s === 'workspace' ? 'Workspace' : 'Personal'}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* TYPE — only for add */}
          {!isRotate && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs font-medium">Type</Label>
              <div className="flex gap-3">
                {[true, false].map((s) => (
                  <label key={String(s)} className="flex cursor-pointer items-center gap-1.5 text-sm">
                    <input
                      type="radio"
                      name="type"
                      checked={isSecret === s}
                      onChange={() => setIsSecret(s)}
                    />
                    {s ? 'Secret' : 'Plain text'}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* VALUE */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="env-value" className="text-xs font-medium">
              {isRotate ? 'New secret value' : 'Value'}
            </Label>
            {isSecret ? (
              <Input
                id="env-value"
                type="password"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className="font-mono text-sm"
                placeholder="••••••••"
                autoComplete="off"
              />
            ) : (
              <Input
                id="env-value"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className="font-mono text-sm"
                maxLength={4096}
              />
            )}
          </div>

          {/* HOSTS — only for secrets */}
          {isSecret && !isRotate && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="env-hosts" className="text-xs font-medium">
                Allowed hosts{' '}
                <span className="font-normal text-muted-foreground">(space or comma separated)</span>
              </Label>
              <Input
                id="env-hosts"
                value={hostsRaw}
                onChange={(e) => setHostsRaw(e.target.value)}
                onBlur={() => setHostsError(validateHosts(hostsRaw))}
                placeholder="api.github.com *.example.com"
                className="text-sm"
              />
              {hostsError && <p className="text-xs text-destructive">{hostsError}</p>}
            </div>
          )}

          {/* Submit error */}
          {submitError && (
            <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {submitError}
            </p>
          )}

          {/* Footer */}
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={saving}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={saving}>
              {saving ? 'Saving…' : isRotate ? 'Rotate' : 'Add'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
```

- [ ] **Step 5.2: TypeScript check**

```bash
cd frontend && pnpm --filter web exec tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5.3: Commit**

```bash
git add "frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/EnvModal.tsx"
git commit -m "feat(sandbox-env-ui): EnvModal component (add + rotate)"
```

---

## Task 6: `EnvTable` component

**Files:**
- Create: `frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/EnvTable.tsx`

- [ ] **Step 6.1: Create `EnvTable`**

```typescript
// frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/EnvTable.tsx
'use client'

import { type EnvEntryOut } from '@cubeplex/core'
import { cn } from '@/lib/utils'
import { WarningCell } from './WarningCell'

export type TableMode = 'org' | 'workspace-admin' | 'workspace-member'

interface Props {
  mode: TableMode
  entries: EnvEntryOut[]
  loading: boolean
  error: string | null
  onRotate: (entry: EnvEntryOut) => void
  onDelete: (entry: EnvEntryOut) => void
}

function ScopeBadge({ scope }: { scope: 'workspace' | 'user' }) {
  if (scope === 'workspace') {
    return (
      <span className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-violet-100 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">
        ws
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-sky-100 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
      me
    </span>
  )
}

export function EnvTable({ mode, entries, loading, error, onRotate, onDelete }: Props) {
  const showScope = mode !== 'org'

  if (loading) {
    return (
      <div className="rounded-xl border border-border/70 bg-card/40 p-5 text-xs text-muted-foreground">
        Loading…
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-xs text-destructive">
        Failed to load: {error}
      </div>
    )
  }

  if (entries.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border/60 bg-muted/20 p-6 text-center text-xs text-muted-foreground">
        No environment variables yet. Add a secret or plain value to inject it into your sandbox.
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-xl border border-border/70">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border/70 bg-muted/40">
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
              Name
            </th>
            {showScope && (
              <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
                Scope
              </th>
            )}
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
              Type
            </th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
              Hosts
            </th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
              Warnings
            </th>
            <th className="px-4 py-2.5 text-right font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
              Actions
            </th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, i) => (
            <tr
              key={entry.id}
              className={cn(
                'border-b border-border/50 last:border-0',
                i % 2 === 0 ? 'bg-background' : 'bg-muted/20',
              )}
            >
              <td className="px-4 py-2.5 font-mono text-xs">{entry.env_name}</td>
              {showScope && (
                <td className="px-4 py-2.5">
                  <ScopeBadge scope={entry.scope as 'workspace' | 'user'} />
                </td>
              )}
              <td className="px-4 py-2.5 text-muted-foreground">
                {entry.is_secret ? 'secret' : 'plain'}
              </td>
              <td className="px-4 py-2.5 text-muted-foreground">
                {entry.hosts && entry.hosts.length > 0 ? entry.hosts.join(', ') : '—'}
              </td>
              <td className="px-4 py-2.5">
                <WarningCell warnings={entry.warnings} />
              </td>
              <td className="px-4 py-2.5 text-right">
                <div className="flex items-center justify-end gap-3">
                  {entry.is_secret && (
                    <button
                      onClick={() => onRotate(entry)}
                      className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                      rotate
                    </button>
                  )}
                  <button
                    onClick={() => onDelete(entry)}
                    className="text-muted-foreground hover:text-destructive transition-colors"
                  >
                    delete
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 6.2: TypeScript check**

```bash
cd frontend && pnpm --filter web exec tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6.3: Commit**

```bash
git add "frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/EnvTable.tsx"
git commit -m "feat(sandbox-env-ui): EnvTable component"
```

---

## Task 7: Admin page

**Files:**
- Create: `frontend/packages/web/app/admin/sandbox-env/page.tsx`

- [ ] **Step 7.1: Create admin page**

```typescript
// frontend/packages/web/app/admin/sandbox-env/page.tsx
'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createApiClient,
  createAdminEnv,
  deleteAdminEnv,
  listAdminEnv,
  rotateAdminEnv,
  type CreateEnvIn,
  type EnvEntryOut,
} from '@cubeplex/core'
import { EnvTable } from '../../(app)/w/[wsId]/sandbox-env/_components/EnvTable'
import { EnvModal, type ModalMode } from '../../(app)/w/[wsId]/sandbox-env/_components/EnvModal'

export default function AdminSandboxEnvPage() {
  const client = useMemo(() => createApiClient(''), [])
  const [entries, setEntries] = useState<EnvEntryOut[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalMode | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const data = await listAdminEnv(client)
      setEntries(data.entries.slice().sort((a, b) => a.env_name.localeCompare(b.env_name)))
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => { load() }, [load])

  async function handleSubmit(
    body: CreateEnvIn | { secret_value: string },
    entryId?: string,
    _scope?: 'workspace' | 'user',
  ) {
    if (entryId) {
      await rotateAdminEnv(client, entryId, body as { secret_value: string })
    } else {
      await createAdminEnv(client, body as CreateEnvIn)
    }
    await load()
  }

  async function handleDelete(entry: EnvEntryOut) {
    if (!confirm(`Delete ${entry.env_name}?`)) return
    await deleteAdminEnv(client, entry.id)
    await load()
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Sandbox environment variables</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Org-wide secrets and plain values injected into every workspace sandbox.
            </p>
          </div>
          <button
            onClick={() => setModal({ kind: 'add-org' })}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/70 bg-background px-3 text-xs font-medium shadow-sm transition-colors hover:bg-accent"
          >
            + Add secret
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl">
          <EnvTable
            mode="org"
            entries={entries}
            loading={loading}
            error={loadError}
            onRotate={(entry) => setModal({ kind: 'rotate', entry })}
            onDelete={handleDelete}
          />
        </div>
      </div>

      {modal && (
        <EnvModal mode={modal} onSubmit={handleSubmit} onClose={() => setModal(null)} />
      )}
    </div>
  )
}
```

- [ ] **Step 7.2: TypeScript check**

```bash
cd frontend && pnpm --filter web exec tsc --noEmit
```

Expected: no errors.

- [ ] **Step 7.3: Commit**

```bash
git add frontend/packages/web/app/admin/sandbox-env/page.tsx
git commit -m "feat(sandbox-env-ui): admin sandbox env page"
```

---

## Task 8: Workspace page

**Files:**
- Create: `frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/page.tsx`

- [ ] **Step 8.1: Create workspace page**

```typescript
// frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/page.tsx
'use client'

import { use, useCallback, useEffect, useMemo, useState } from 'react'
import {
  createApiClient,
  createWsEnvMe,
  createWsEnvWorkspace,
  deleteWsEnvMe,
  deleteWsEnvWorkspace,
  listWsEnvMe,
  listWsEnvWorkspace,
  rotateWsEnvMe,
  rotateWsEnvWorkspace,
  type CreateEnvIn,
  type EnvEntryOut,
} from '@cubeplex/core'
import { useWorkspaceStore } from '@cubeplex/core'
import { EnvTable } from './_components/EnvTable'
import { EnvModal, type ModalMode } from './_components/EnvModal'

interface PageProps {
  params: Promise<{ wsId: string }>
}

export default function WorkspaceSandboxEnvPage({ params }: PageProps): React.ReactElement {
  const { wsId } = use(params)
  const client = useMemo(() => createApiClient(''), [])
  const wsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)
  const isAdmin = wsRole === 'admin'

  const [entries, setEntries] = useState<EnvEntryOut[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalMode | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const fetches = isAdmin
        ? await Promise.all([listWsEnvWorkspace(client, wsId), listWsEnvMe(client, wsId)])
        : [await listWsEnvMe(client, wsId)]
      const merged = fetches
        .flatMap((r) => r.entries)
        .sort((a, b) => a.env_name.localeCompare(b.env_name))
      setEntries(merged)
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [client, wsId, isAdmin])

  useEffect(() => { load() }, [load])

  async function handleSubmit(
    body: CreateEnvIn | { secret_value: string },
    entryId?: string,
    scope?: 'workspace' | 'user',
  ) {
    if (entryId) {
      // Rotate: find the entry to determine which path to call
      const entry = entries.find((e) => e.id === entryId)
      if (entry?.scope === 'workspace') {
        await rotateWsEnvWorkspace(client, wsId, entryId, body as { secret_value: string })
      } else {
        await rotateWsEnvMe(client, wsId, entryId, body as { secret_value: string })
      }
    } else {
      // Add: use the scope the user selected inside the modal
      const createBody = body as CreateEnvIn
      if (scope === 'workspace') {
        await createWsEnvWorkspace(client, wsId, createBody)
      } else {
        await createWsEnvMe(client, wsId, createBody)
      }
    }
    await load()
  }

  async function handleDelete(entry: EnvEntryOut) {
    if (!confirm(`Delete ${entry.env_name}?`)) return
    if (entry.scope === 'workspace') {
      await deleteWsEnvWorkspace(client, wsId, entry.id)
    } else {
      await deleteWsEnvMe(client, wsId, entry.id)
    }
    await load()
  }

  const tableMode = isAdmin ? 'workspace-admin' : 'workspace-member'

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Sandbox environment variables</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Secrets and plain values injected into your sandbox.
            </p>
          </div>
          <div className="flex gap-2">
            {isAdmin && (
              <button
                onClick={() => setModal({ kind: 'add-workspace', defaultScope: 'workspace' })}
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/70 bg-background px-3 text-xs font-medium shadow-sm transition-colors hover:bg-accent"
              >
                + Workspace secret
              </button>
            )}
            <button
              onClick={() => setModal({ kind: 'add-workspace', defaultScope: 'user' })}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/70 bg-background px-3 text-xs font-medium shadow-sm transition-colors hover:bg-accent"
            >
              + Personal secret
            </button>
          </div>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl">
          <EnvTable
            mode={tableMode}
            entries={entries}
            loading={loading}
            error={loadError}
            onRotate={(entry) => setModal({ kind: 'rotate', entry })}
            onDelete={handleDelete}
          />
        </div>
      </div>

      {modal && (
        <EnvModal mode={modal} onSubmit={handleSubmit} onClose={() => setModal(null)} />
      )}
    </div>
  )
}
```

- [ ] **Step 8.2: TypeScript check**

```bash
cd frontend && pnpm --filter web exec tsc --noEmit
```

Expected: no errors.

- [ ] **Step 8.3: Commit**

```bash
git add "frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/page.tsx"
git commit -m "feat(sandbox-env-ui): workspace sandbox env page (role-aware)"
```

---

## Task 9: End-to-end verification

- [ ] **Step 9.1: Start backend and frontend in the worktree**

```bash
# Backend (in worktree backend/)
cat .worktree.env   # note CUBEPLEX_API__PORT (e.g. 8050)
uv run python main.py

# Frontend (in worktree frontend/)
pnpm dev            # reads PORT from .worktree.env via with-worktree-env.mjs
```

- [ ] **Step 9.2: Verify admin page**

Open `http://192.168.1.111:<PORT>/admin/sandbox-env`.

Check:
- "Sandbox env" appears in admin sidebar after "Sandbox policy"
- Page loads with empty state message
- Click "+ Add secret" → modal opens with NAME / TYPE / VALUE / HOSTS fields
- Fill in `TEST_TOKEN`, Secret, a value, host `api.example.com` → submit
- Row appears in table with name `TEST_TOKEN`, type `secret`, host `api.example.com`
- Click `rotate` → modal opens with NAME pre-filled and locked → enter new value → submit → row still present
- Click `delete` → confirm → row disappears

- [ ] **Step 9.3: Verify workspace page as admin**

Log in as workspace admin. Open `http://192.168.1.111:<PORT>/w/<wsId>/sandbox-env`.

Check:
- "Sandbox env" appears in workspace sidebar
- Both "+ Workspace secret" and "+ Personal secret" buttons present
- Add a workspace-scope secret and a personal secret
- Both rows appear with correct SCOPE badge (`ws` violet / `me` sky)
- Rotate and delete both

- [ ] **Step 9.4: Verify workspace page as non-admin member**

Log in as a workspace member (non-admin).

Check:
- Only "+ Personal secret" button visible
- Only user-scope entries visible (no `ws` rows)
- Can add, rotate, delete personal secrets

- [ ] **Step 9.5: Final full lint + type check**

```bash
cd frontend && pnpm --filter web run lint && pnpm --filter web exec tsc --noEmit
```

Expected: no errors.

- [ ] **Step 9.6: Commit and push**

```bash
cd frontend && git add -A && git commit -m "chore: post-integration verification pass"
```
