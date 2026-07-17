# MCP Tools Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `MCPServerDetail` with a documentation-grade master-detail tools browser.

**Architecture:** The frontend is split into a `ServerHero` + `ServerErrorBanner` + `OverviewPanel` + `ToolsPanel` tree. `ToolsPanel` is a master-detail (`ToolList` left, `ToolDetail` right) with a Schema / Try-it / JSON view switch. `SchemaView` is a recursive renderer over JSON Schema; `TryItView` is a disabled UI shell — backend invoke endpoint is a follow-up.

> The originally-paired backend bug (empty `input_schema` from `langchain-mcp-adapters`) was fixed upstream by PR #95 (replaced `cubeplex/mcp/discovery.py` with `cubepi_admin_discovery.py` using the raw `mcp` SDK). Task 1 below is preserved for historical record but was dropped during the rebase onto main.

**Tech Stack:** Next.js 16 / React 19 / TypeScript / Tailwind 4 / shadcn-ui / next-intl.

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/mcp-tools-redesign` (slot 19, backend 8019, frontend 3019).

**Reference spec:** `docs/superpowers/specs/2026-05-14-mcp-tools-redesign-design.md`.

---

## File map

**Frontend (create):**
- `frontend/packages/web/components/mcp/detail/ServerHero.tsx`
- `frontend/packages/web/components/mcp/detail/ServerErrorBanner.tsx`
- `frontend/packages/web/components/mcp/detail/OverviewPanel.tsx`
- `frontend/packages/web/components/mcp/detail/tools/ToolsPanel.tsx`
- `frontend/packages/web/components/mcp/detail/tools/ToolList.tsx`
- `frontend/packages/web/components/mcp/detail/tools/ToolDetail.tsx`
- `frontend/packages/web/components/mcp/detail/tools/SchemaView.tsx`
- `frontend/packages/web/components/mcp/detail/tools/SchemaParameterRow.tsx`
- `frontend/packages/web/components/mcp/detail/tools/TryItView.tsx`
- `frontend/packages/web/components/mcp/detail/tools/JsonView.tsx`
- `frontend/packages/web/lib/jsonSchemaTypes.ts` — type-name + chip-color helpers

**Frontend (modify):**
- `frontend/packages/web/components/mcp/MCPServerDetail.tsx` — slim down, compose new tree
- `frontend/packages/web/messages/en.json` — new translation keys
- `frontend/packages/web/messages/zh.json` — new translation keys

**Frontend (delete):**
- `frontend/packages/web/components/mcp/MCPToolsTable.tsx`

---

## Task 1: ~~Backend `serialize_tool` dict-schema fix~~ (obsolete)

Superseded by PR #95 — `cubeplex/mcp/discovery.py` and `tests/unit/test_discovery_serialize.py` were both removed from main when admin discovery was ported to `cubeplex/mcp/cubepi_admin_discovery.py` (raw `mcp` SDK). Nothing to do on this branch.

The task commit was auto-dropped as empty during the rebase.

---

## Task 2: Translation keys (en + zh)

**Files:**
- Modify: `frontend/packages/web/messages/en.json` (under `mcp.detail` and `mcp.tools`)
- Modify: `frontend/packages/web/messages/zh.json` (same)

- [ ] **Step 1: Add new English keys**

In `frontend/packages/web/messages/en.json`, replace the existing `"mcp": { "tools": {...}, "detail": {...} }` sections with the merged versions below. Keep all sibling keys under `mcp` (e.g. `scopeBadge`, `connector`) untouched.

```json
"detail": {
  "notDiscoveredYet": "not discovered yet",
  "lastDiscoveredLine": "{transport} · last synced {time}",
  "refreshTools": "Refresh",
  "shareToOrg": "Share to org",
  "delete": "Delete",
  "overview": "Overview",
  "toolsTab": "Tools ({count})",
  "workspaces": "Workspaces",
  "serverDetails": "Server details",
  "url": "URL",
  "transport": "Transport",
  "authMethod": "Auth method",
  "credentialScope": "Credential scope",
  "timeout": "Timeout",
  "timeoutValue": "{timeout}s / SSE {sseTimeout}s",
  "statusConnected": "Connected",
  "statusDisconnected": "Disconnected",
  "toolsCount": "{count, plural, one {# tool} other {# tools}}",
  "metaLine": "Last synced {time} · {tools}",
  "metaLineNever": "Never synced · {tools}",
  "copy": "Copy",
  "copied": "Copied",
  "connectionCard": {
    "title": "Connection",
    "url": "URL",
    "transport": "Transport",
    "authMethod": "Auth method",
    "scope": "Scope",
    "timeouts": "Timeouts",
    "timeoutsValue": "Request {timeout}s · SSE {sseTimeout}s"
  },
  "errorBanner": {
    "title": "Last discovery failed",
    "expand": "Show details",
    "collapse": "Hide details"
  }
},
"tools": {
  "empty": "No tools discovered yet — click Refresh in the header.",
  "emptyMatch": "No tools match \"{query}\".",
  "filterPlaceholder": "Filter tools…",
  "countAll": "{count, plural, one {# tool} other {# tools}}",
  "countMatch": "{matched} of {total} match",
  "argsSummary": "{count, plural, one {# arg} other {# args}}",
  "requiredSummary": "{count} required",
  "detail": {
    "viewSchema": "Schema",
    "viewTryIt": "Try it",
    "viewJson": "JSON",
    "schema": {
      "noParams": "This tool takes no parameters.",
      "parameters": "Parameters",
      "required": "Required",
      "defaultLabel": "default:",
      "allowed": "Allowed:",
      "itemShape": "Item shape",
      "variantLabel": "Variant {n}",
      "unresolvedRef": "$ref",
      "malformed": "Unable to render schema. Use the JSON tab to inspect."
    },
    "tryit": {
      "banner": "Run this tool with custom arguments. Coming soon — backend in next PR.",
      "run": "Run",
      "runDisabledTooltip": "Try-it backend not yet available",
      "jsonHint": "Enter JSON"
    },
    "json": {
      "copy": "Copy JSON",
      "copied": "Schema copied"
    }
  }
}
```

- [ ] **Step 2: Add the matching Chinese keys**

In `frontend/packages/web/messages/zh.json`, replace the existing `mcp.detail` and `mcp.tools` blocks with:

```json
"detail": {
  "notDiscoveredYet": "尚未发现",
  "lastDiscoveredLine": "{transport} · 上次同步 {time}",
  "refreshTools": "刷新",
  "shareToOrg": "共享到组织",
  "delete": "删除",
  "overview": "概览",
  "toolsTab": "工具 ({count})",
  "workspaces": "工作区",
  "serverDetails": "服务详情",
  "url": "URL",
  "transport": "传输方式",
  "authMethod": "认证方式",
  "credentialScope": "凭证作用域",
  "timeout": "超时",
  "timeoutValue": "{timeout} 秒 / SSE {sseTimeout} 秒",
  "statusConnected": "已连接",
  "statusDisconnected": "未连接",
  "toolsCount": "{count} 个工具",
  "metaLine": "上次同步 {time} · {tools}",
  "metaLineNever": "尚未同步 · {tools}",
  "copy": "复制",
  "copied": "已复制",
  "connectionCard": {
    "title": "连接信息",
    "url": "URL",
    "transport": "传输方式",
    "authMethod": "认证方式",
    "scope": "作用域",
    "timeouts": "超时",
    "timeoutsValue": "请求 {timeout} 秒 · SSE {sseTimeout} 秒"
  },
  "errorBanner": {
    "title": "上次发现失败",
    "expand": "展开详情",
    "collapse": "收起详情"
  }
},
"tools": {
  "empty": "尚未发现任何工具 — 请在顶部点击 “刷新”。",
  "emptyMatch": "没有匹配 “{query}” 的工具。",
  "filterPlaceholder": "搜索工具…",
  "countAll": "{count} 个工具",
  "countMatch": "{matched} / {total} 匹配",
  "argsSummary": "{count} 个参数",
  "requiredSummary": "{count} 个必填",
  "detail": {
    "viewSchema": "Schema",
    "viewTryIt": "试运行",
    "viewJson": "JSON",
    "schema": {
      "noParams": "该工具不需要参数。",
      "parameters": "参数",
      "required": "必填",
      "defaultLabel": "默认:",
      "allowed": "允许值:",
      "itemShape": "元素结构",
      "variantLabel": "变体 {n}",
      "unresolvedRef": "$ref",
      "malformed": "无法渲染 schema，请切到 JSON 标签查看。"
    },
    "tryit": {
      "banner": "用自定义参数调用该工具。即将上线 —— 后端将在下个 PR 提供。",
      "run": "运行",
      "runDisabledTooltip": "Try-it 后端尚未上线",
      "jsonHint": "输入 JSON"
    },
    "json": {
      "copy": "复制 JSON",
      "copied": "已复制 Schema"
    }
  }
}
```

- [ ] **Step 3: Type-check (verifies JSON is valid)**

```bash
cd frontend
pnpm type-check
```

Expected: no errors. (next-intl uses messages at runtime, but tsc still parses the surrounding modules.)

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/messages/en.json frontend/packages/web/messages/zh.json
git commit -m "chore(mcp): add i18n keys for MCP detail and tools redesign"
```

---

## Task 3: JSON Schema type helper

**Files:**
- Create: `frontend/packages/web/lib/jsonSchemaTypes.ts`

- [ ] **Step 1: Create the helper module**

```ts
// frontend/packages/web/lib/jsonSchemaTypes.ts
export type SchemaNode = Record<string, unknown>

export interface ResolvedType {
  label: string
  kind: 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array' | 'any'
}

export function resolveType(node: SchemaNode): ResolvedType {
  const t = node.type
  if (t === 'string') return { label: 'string', kind: 'string' }
  if (t === 'integer') return { label: 'integer', kind: 'integer' }
  if (t === 'number') return { label: 'number', kind: 'number' }
  if (t === 'boolean') return { label: 'boolean', kind: 'boolean' }
  if (t === 'object') return { label: 'object', kind: 'object' }
  if (t === 'array') {
    const items = (node.items ?? {}) as SchemaNode
    const inner = resolveType(items)
    return { label: `array<${inner.label}>`, kind: 'array' }
  }
  if (Array.isArray(t) && t.length > 0) {
    const first = resolveType({ type: t[0] } as SchemaNode)
    return { label: t.join(' | '), kind: first.kind }
  }
  return { label: 'any', kind: 'any' }
}

const KIND_TO_CHIP: Record<ResolvedType['kind'], string> = {
  string: 'bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300',
  number: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  integer: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  boolean: 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300',
  object: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300',
  array: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
  any: 'bg-muted text-muted-foreground',
}

export function typeChipClasses(kind: ResolvedType['kind']): string {
  return KIND_TO_CHIP[kind]
}

export function resolveRef(root: SchemaNode, ref: string): SchemaNode | null {
  // Supports "#/definitions/Foo" and "#/$defs/Foo"
  if (!ref.startsWith('#/')) return null
  const parts = ref.slice(2).split('/')
  let node: unknown = root
  for (const p of parts) {
    if (node && typeof node === 'object' && p in (node as Record<string, unknown>)) {
      node = (node as Record<string, unknown>)[p]
    } else {
      return null
    }
  }
  return (node as SchemaNode) ?? null
}

export function isObjectSchema(node: SchemaNode): boolean {
  return node.type === 'object' || typeof node.properties === 'object'
}

export function getProperties(node: SchemaNode): Record<string, SchemaNode> {
  const props = node.properties
  return (props && typeof props === 'object' ? props : {}) as Record<string, SchemaNode>
}

export function getRequired(node: SchemaNode): string[] {
  return Array.isArray(node.required) ? (node.required as string[]) : []
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/lib/jsonSchemaTypes.ts
git commit -m "feat(mcp): json-schema type helpers (resolve, chip palette, ref lookup)"
```

---

## Task 4: SchemaParameterRow + SchemaView (primitives, enums, required, default)

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/tools/SchemaParameterRow.tsx`
- Create: `frontend/packages/web/components/mcp/detail/tools/SchemaView.tsx`

This task gets primitives + enum + required + default working. Nesting / arrays / oneOf / refs come in Task 5.

- [ ] **Step 1: Create `SchemaParameterRow.tsx`**

```tsx
// frontend/packages/web/components/mcp/detail/tools/SchemaParameterRow.tsx
'use client'

import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'
import { resolveType, typeChipClasses, type SchemaNode } from '@/lib/jsonSchemaTypes'

export interface SchemaParameterRowProps {
  name: string
  node: SchemaNode
  required: boolean
  root: SchemaNode
  depth?: number
}

export function SchemaParameterRow({ name, node, required }: SchemaParameterRowProps) {
  const t = useTranslations('mcp.tools.detail.schema')
  const resolved = resolveType(node)
  const description = typeof node.description === 'string' ? node.description : null
  const defaultValue = node.default
  const enumValues = Array.isArray(node.enum) ? (node.enum as unknown[]) : null

  return (
    <div className="flex flex-col gap-1 border-b border-border/40 px-4 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm font-semibold">{name}</span>
        <span
          className={cn(
            'rounded-md px-1.5 py-0.5 font-mono text-[11px] leading-none',
            typeChipClasses(resolved.kind),
          )}
        >
          {resolved.label}
        </span>
        {required ? (
          <span className="rounded-md bg-destructive/15 px-1.5 py-0.5 text-[11px] font-medium leading-none text-destructive">
            {t('required')}
          </span>
        ) : null}
        {defaultValue !== undefined ? (
          <span className="text-[11px] text-muted-foreground">
            {t('defaultLabel')}{' '}
            <code className="rounded bg-muted px-1 py-0.5 font-mono">
              {JSON.stringify(defaultValue)}
            </code>
          </span>
        ) : null}
      </div>
      {description ? (
        <p className="text-sm text-muted-foreground">{description}</p>
      ) : null}
      {enumValues ? (
        <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
          <span className="text-[11px] text-muted-foreground">{t('allowed')}</span>
          {enumValues.map((value, i) => (
            <code
              key={`${name}-enum-${i}`}
              className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]"
            >
              {JSON.stringify(value)}
            </code>
          ))}
        </div>
      ) : null}
    </div>
  )
}
```

- [ ] **Step 2: Create `SchemaView.tsx`**

```tsx
// frontend/packages/web/components/mcp/detail/tools/SchemaView.tsx
'use client'

import { useTranslations } from 'next-intl'

import { getProperties, getRequired, isObjectSchema, type SchemaNode } from '@/lib/jsonSchemaTypes'

import { SchemaParameterRow } from './SchemaParameterRow'

export interface SchemaViewProps {
  schema: SchemaNode | null | undefined
}

export function SchemaView({ schema }: SchemaViewProps) {
  const t = useTranslations('mcp.tools.detail.schema')

  if (!schema || typeof schema !== 'object') {
    return <p className="px-4 py-6 text-sm text-muted-foreground">{t('noParams')}</p>
  }

  if (!isObjectSchema(schema)) {
    return <p className="px-4 py-6 text-sm text-muted-foreground">{t('malformed')}</p>
  }

  const properties = getProperties(schema)
  const required = new Set(getRequired(schema))
  const entries = Object.entries(properties)

  if (entries.length === 0) {
    return <p className="px-4 py-6 text-sm text-muted-foreground">{t('noParams')}</p>
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="border-b border-border/60 bg-muted/40 px-4 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {t('parameters')}
      </div>
      <div className="flex flex-col">
        {entries.map(([name, node]) => (
          <SchemaParameterRow
            key={name}
            name={name}
            node={node}
            required={required.has(name)}
            root={schema}
          />
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/tools/SchemaParameterRow.tsx \
        frontend/packages/web/components/mcp/detail/tools/SchemaView.tsx
git commit -m "feat(mcp): schema view with parameter rows (primitives, enums, defaults)"
```

---

## Task 5: Recursive nesting in SchemaParameterRow (object / array / oneOf / $ref)

**Files:**
- Modify: `frontend/packages/web/components/mcp/detail/tools/SchemaParameterRow.tsx`

Extends Task 4's row to handle nested schemas. Keep the existing flat rendering for primitives.

- [ ] **Step 1: Replace `SchemaParameterRow.tsx` with the recursive version**

```tsx
// frontend/packages/web/components/mcp/detail/tools/SchemaParameterRow.tsx
'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'
import {
  getProperties,
  getRequired,
  resolveRef,
  resolveType,
  typeChipClasses,
  type SchemaNode,
} from '@/lib/jsonSchemaTypes'

export interface SchemaParameterRowProps {
  name: string
  node: SchemaNode
  required: boolean
  root: SchemaNode
  depth?: number
}

const MAX_AUTO_EXPAND_DEPTH = 1

function resolveNode(node: SchemaNode, root: SchemaNode): {
  resolved: SchemaNode
  unresolvedRef: string | null
} {
  if (typeof node.$ref === 'string') {
    const target = resolveRef(root, node.$ref)
    if (target) return { resolved: target, unresolvedRef: null }
    return { resolved: node, unresolvedRef: node.$ref }
  }
  return { resolved: node, unresolvedRef: null }
}

export function SchemaParameterRow({
  name,
  node,
  required,
  root,
  depth = 0,
}: SchemaParameterRowProps) {
  const t = useTranslations('mcp.tools.detail.schema')
  const { resolved, unresolvedRef } = resolveNode(node, root)
  const variants = Array.isArray(resolved.oneOf)
    ? (resolved.oneOf as SchemaNode[])
    : Array.isArray(resolved.anyOf)
      ? (resolved.anyOf as SchemaNode[])
      : null
  const [activeVariant, setActiveVariant] = useState(0)
  const [expanded, setExpanded] = useState(depth < MAX_AUTO_EXPAND_DEPTH)

  const effective: SchemaNode = variants ? variants[activeVariant] ?? resolved : resolved
  const typeInfo = resolveType(effective)
  const description = typeof resolved.description === 'string' ? resolved.description : null
  const defaultValue = resolved.default
  const enumValues = Array.isArray(effective.enum) ? (effective.enum as unknown[]) : null

  const hasNestedObject =
    typeInfo.kind === 'object' && Object.keys(getProperties(effective)).length > 0
  const arrayItems =
    typeInfo.kind === 'array' && typeof effective.items === 'object'
      ? (effective.items as SchemaNode)
      : null
  const arrayHasObjectItems =
    arrayItems !== null &&
    (arrayItems.type === 'object' || typeof arrayItems.properties === 'object')

  const expandable = hasNestedObject || arrayHasObjectItems

  return (
    <div className="border-b border-border/40 last:border-b-0">
      <div className="flex flex-col gap-1 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          {expandable ? (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="text-muted-foreground transition hover:text-foreground"
              aria-label={expanded ? 'collapse' : 'expand'}
            >
              {expanded ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </button>
          ) : null}
          <span className="font-mono text-sm font-semibold">{name}</span>
          <span
            className={cn(
              'rounded-md px-1.5 py-0.5 font-mono text-[11px] leading-none',
              typeChipClasses(typeInfo.kind),
            )}
          >
            {typeInfo.label}
          </span>
          {required ? (
            <span className="rounded-md bg-destructive/15 px-1.5 py-0.5 text-[11px] font-medium leading-none text-destructive">
              {t('required')}
            </span>
          ) : null}
          {defaultValue !== undefined ? (
            <span className="text-[11px] text-muted-foreground">
              {t('defaultLabel')}{' '}
              <code className="rounded bg-muted px-1 py-0.5 font-mono">
                {JSON.stringify(defaultValue)}
              </code>
            </span>
          ) : null}
          {unresolvedRef ? (
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
              {t('unresolvedRef')}: {unresolvedRef}
            </code>
          ) : null}
        </div>

        {description ? (
          <p className="text-sm text-muted-foreground">{description}</p>
        ) : null}

        {enumValues ? (
          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
            <span className="text-[11px] text-muted-foreground">{t('allowed')}</span>
            {enumValues.map((value, i) => (
              <code
                key={`${name}-enum-${i}`}
                className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]"
              >
                {JSON.stringify(value)}
              </code>
            ))}
          </div>
        ) : null}

        {variants ? (
          <div className="flex flex-wrap items-center gap-1 pt-1">
            {variants.map((_, i) => (
              <button
                key={`${name}-variant-${i}`}
                type="button"
                onClick={() => setActiveVariant(i)}
                className={cn(
                  'rounded-md border px-2 py-0.5 text-[11px] transition',
                  i === activeVariant
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border text-muted-foreground hover:border-primary/50',
                )}
              >
                {t('variantLabel', { n: i + 1 })}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {expandable && expanded ? (
        <div className="ml-4 border-l border-border/60 pl-2">
          {hasNestedObject
            ? renderNestedObject(effective, root, depth + 1)
            : null}
          {arrayHasObjectItems && arrayItems
            ? renderArrayItems(arrayItems, root, depth + 1, t('itemShape'))
            : null}
        </div>
      ) : null}
    </div>
  )
}

function renderNestedObject(node: SchemaNode, root: SchemaNode, depth: number) {
  const props = getProperties(node)
  const req = new Set(getRequired(node))
  return Object.entries(props).map(([childName, childNode]) => (
    <SchemaParameterRow
      key={childName}
      name={childName}
      node={childNode}
      required={req.has(childName)}
      root={root}
      depth={depth}
    />
  ))
}

function renderArrayItems(items: SchemaNode, root: SchemaNode, depth: number, label: string) {
  return (
    <div>
      <div className="px-4 py-2 text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      {renderNestedObject(items, root, depth)}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/tools/SchemaParameterRow.tsx
git commit -m "feat(mcp): schema rows handle nested objects, arrays, oneOf, and \$ref"
```

---

## Task 6: JsonView (pretty JSON + copy)

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/tools/JsonView.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/packages/web/components/mcp/detail/tools/JsonView.tsx
'use client'

import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'

export interface JsonViewProps {
  schema: unknown
}

export function JsonView({ schema }: JsonViewProps) {
  const t = useTranslations('mcp.tools.detail.json')
  const [copied, setCopied] = useState(false)
  const pretty = JSON.stringify(schema ?? {}, null, 2)

  async function handleCopy(): Promise<void> {
    await navigator.clipboard.writeText(pretty)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="relative overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border/60 bg-muted/40 px-3 py-2">
        <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          input_schema
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void handleCopy()}
          className="h-7"
        >
          {copied ? (
            <Check data-icon="inline-start" className="h-3.5 w-3.5" />
          ) : (
            <Copy data-icon="inline-start" className="h-3.5 w-3.5" />
          )}
          {copied ? t('copied') : t('copy')}
        </Button>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-xs leading-relaxed">{pretty}</pre>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/tools/JsonView.tsx
git commit -m "feat(mcp): json view for tool input_schema with copy"
```

---

## Task 7: TryItView (disabled UI shell)

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/tools/TryItView.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/packages/web/components/mcp/detail/tools/TryItView.tsx
'use client'

import { useMemo, useState } from 'react'
import { Play } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  getProperties,
  getRequired,
  resolveType,
  type SchemaNode,
} from '@/lib/jsonSchemaTypes'

export interface TryItViewProps {
  toolName: string
  schema: SchemaNode | null | undefined
}

type FieldValue = string | number | boolean

export function TryItView({ toolName, schema }: TryItViewProps) {
  const t = useTranslations('mcp.tools.detail.tryit')
  const properties = useMemo(
    () => (schema && typeof schema === 'object' ? getProperties(schema) : {}),
    [schema],
  )
  const required = useMemo(
    () => new Set(schema && typeof schema === 'object' ? getRequired(schema) : []),
    [schema],
  )

  const [values, setValues] = useState<Record<string, FieldValue>>({})

  function setValue(name: string, v: FieldValue): void {
    setValues((prev) => ({ ...prev, [name]: v }))
  }

  const entries = Object.entries(properties)

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border border-dashed border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
        {t('banner')}
      </div>

      {entries.length === 0 ? null : (
        <div className="flex flex-col gap-4">
          {entries.map(([name, node]) => (
            <FieldRow
              key={`${toolName}-${name}`}
              name={name}
              node={node}
              required={required.has(name)}
              value={values[name]}
              onChange={(v) => setValue(name, v)}
            />
          ))}
        </div>
      )}

      <div className="flex justify-end">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0}>
                <Button type="button" disabled>
                  <Play data-icon="inline-start" />
                  {t('run')}
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>{t('runDisabledTooltip')}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
    </div>
  )
}

function FieldRow({
  name,
  node,
  required,
  value,
  onChange,
}: {
  name: string
  node: SchemaNode
  required: boolean
  value: FieldValue | undefined
  onChange: (v: FieldValue) => void
}) {
  const t = useTranslations('mcp.tools.detail.tryit')
  const typeInfo = resolveType(node)
  const description = typeof node.description === 'string' ? node.description : null
  const enumValues = Array.isArray(node.enum) ? (node.enum as unknown[]) : null

  return (
    <div className="flex flex-col gap-1.5">
      <Label className="flex items-center gap-1.5 text-sm">
        <span className="font-mono">{name}</span>
        {required ? <span className="text-destructive">*</span> : null}
        <span className="text-[11px] text-muted-foreground">{typeInfo.label}</span>
      </Label>
      {enumValues ? (
        <Select
          value={value === undefined ? '' : String(value)}
          onValueChange={(v) => onChange(v)}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {enumValues.map((opt, i) => (
              <SelectItem key={`${name}-opt-${i}`} value={String(opt)}>
                {String(opt)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : typeInfo.kind === 'boolean' ? (
        <Switch checked={Boolean(value)} onCheckedChange={(v) => onChange(v)} />
      ) : typeInfo.kind === 'number' || typeInfo.kind === 'integer' ? (
        <Input
          type="number"
          value={value === undefined ? '' : String(value)}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      ) : typeInfo.kind === 'object' || typeInfo.kind === 'array' ? (
        <Textarea
          rows={3}
          placeholder={t('jsonHint')}
          value={value === undefined ? '' : String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <Input
          type="text"
          value={value === undefined ? '' : String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/tools/TryItView.tsx
git commit -m "feat(mcp): try-it ui shell with disabled run button"
```

---

## Task 8: ToolList (left sidebar + search)

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/tools/ToolList.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/packages/web/components/mcp/detail/tools/ToolList.tsx
'use client'

import type { MCPToolEntry } from '@cubeplex/core'
import { Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'

import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { getProperties, getRequired, type SchemaNode } from '@/lib/jsonSchemaTypes'

export interface ToolListProps {
  tools: MCPToolEntry[]
  selectedName: string | null
  onSelect: (name: string) => void
}

function countArgs(schema: unknown): { args: number; required: number } {
  if (!schema || typeof schema !== 'object') return { args: 0, required: 0 }
  const node = schema as SchemaNode
  return { args: Object.keys(getProperties(node)).length, required: getRequired(node).length }
}

export function ToolList({ tools, selectedName, onSelect }: ToolListProps) {
  const t = useTranslations('mcp.tools')
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return tools
    return tools.filter(
      (tool) =>
        tool.name.toLowerCase().includes(q) ||
        (tool.description ?? '').toLowerCase().includes(q),
    )
  }, [tools, query])

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('filterPlaceholder')}
          className="h-9 pl-7 text-sm"
        />
      </div>
      <p className="px-1 text-xs text-muted-foreground">
        {query
          ? t('countMatch', { matched: filtered.length, total: tools.length })
          : t('countAll', { count: tools.length })}
      </p>
      <ScrollArea className="min-h-0 flex-1">
        <ul className="flex flex-col gap-0.5 pr-1">
          {filtered.length === 0 ? (
            <li className="px-3 py-6 text-center text-xs text-muted-foreground">
              {query ? t('emptyMatch', { query }) : t('empty')}
            </li>
          ) : (
            filtered.map((tool) => {
              const { args, required } = countArgs(tool.input_schema)
              const selected = tool.name === selectedName
              return (
                <li key={tool.name}>
                  <button
                    type="button"
                    onClick={() => onSelect(tool.name)}
                    className={cn(
                      'group flex w-full flex-col gap-1 rounded-md border border-transparent px-3 py-2 text-left transition',
                      selected
                        ? 'border-l-2 border-l-primary bg-primary/5'
                        : 'hover:bg-muted/60',
                    )}
                  >
                    <span className="truncate font-mono text-sm font-semibold">
                      {tool.name}
                    </span>
                    {tool.description ? (
                      <span className="truncate text-xs text-muted-foreground">
                        {tool.description}
                      </span>
                    ) : null}
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                      {t('argsSummary', { count: args })}
                      {required > 0 ? ` · ${t('requiredSummary', { count: required })}` : ''}
                    </span>
                  </button>
                </li>
              )
            })
          )}
        </ul>
      </ScrollArea>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/tools/ToolList.tsx
git commit -m "feat(mcp): tool list with search and selection"
```

---

## Task 9: ToolDetail (right panel + view switch)

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/tools/ToolDetail.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/packages/web/components/mcp/detail/tools/ToolDetail.tsx
'use client'

import type { MCPToolEntry } from '@cubeplex/core'
import { useTranslations } from 'next-intl'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { SchemaNode } from '@/lib/jsonSchemaTypes'

import { JsonView } from './JsonView'
import { SchemaView } from './SchemaView'
import { TryItView } from './TryItView'

export type ToolDetailView = 'schema' | 'tryit' | 'json'

export interface ToolDetailProps {
  tool: MCPToolEntry
  view: ToolDetailView
  onViewChange: (view: ToolDetailView) => void
}

export function ToolDetail({ tool, view, onViewChange }: ToolDetailProps) {
  const t = useTranslations('mcp.tools.detail')
  const schema = (tool.input_schema as SchemaNode | null) ?? null

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="font-mono text-lg font-semibold">{tool.name}</h2>
        {tool.description ? (
          <p className="text-sm text-muted-foreground">{tool.description}</p>
        ) : null}
      </div>

      <Tabs value={view} onValueChange={(v) => onViewChange(v as ToolDetailView)}>
        <TabsList>
          <TabsTrigger value="schema">{t('viewSchema')}</TabsTrigger>
          <TabsTrigger value="tryit">{t('viewTryIt')}</TabsTrigger>
          <TabsTrigger value="json">{t('viewJson')}</TabsTrigger>
        </TabsList>
        <TabsContent value="schema" className="mt-4">
          <SchemaView schema={schema} />
        </TabsContent>
        <TabsContent value="tryit" className="mt-4">
          <TryItView toolName={tool.name} schema={schema} />
        </TabsContent>
        <TabsContent value="json" className="mt-4">
          <JsonView schema={schema} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/tools/ToolDetail.tsx
git commit -m "feat(mcp): tool detail panel with schema/try-it/json switch"
```

---

## Task 10: ToolsPanel (master-detail container)

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/tools/ToolsPanel.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/packages/web/components/mcp/detail/tools/ToolsPanel.tsx
'use client'

import type { MCPToolEntry } from '@cubeplex/core'
import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'

import { ToolDetail, type ToolDetailView } from './ToolDetail'
import { ToolList } from './ToolList'

export interface ToolsPanelProps {
  tools: MCPToolEntry[]
}

export function ToolsPanel({ tools }: ToolsPanelProps) {
  const t = useTranslations('mcp.tools')
  const [selectedName, setSelectedName] = useState<string | null>(
    tools.length > 0 ? tools[0].name : null,
  )
  const [view, setView] = useState<ToolDetailView>('schema')

  useEffect(() => {
    if (tools.length === 0) {
      setSelectedName(null)
      return
    }
    if (!selectedName || !tools.some((tool) => tool.name === selectedName)) {
      setSelectedName(tools[0].name)
    }
  }, [tools, selectedName])

  const selected = tools.find((tool) => tool.name === selectedName) ?? null

  if (tools.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center text-sm text-muted-foreground">
        {t('empty')}
      </div>
    )
  }

  return (
    <div className="grid min-h-[420px] grid-cols-[280px_minmax(0,1fr)] gap-6">
      <aside className="min-h-0 border-r border-border/60 pr-4">
        <ToolList tools={tools} selectedName={selectedName} onSelect={setSelectedName} />
      </aside>
      <section className="min-h-0">
        {selected ? <ToolDetail tool={selected} view={view} onViewChange={setView} /> : null}
      </section>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/tools/ToolsPanel.tsx
git commit -m "feat(mcp): master-detail tools panel with state management"
```

---

## Task 11: ServerErrorBanner

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/ServerErrorBanner.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/packages/web/components/mcp/detail/ServerErrorBanner.tsx
'use client'

import { useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react'
import { useTranslations } from 'next-intl'

export interface ServerErrorBannerProps {
  error: string
}

const SHORT_LIMIT = 140

export function ServerErrorBanner({ error }: ServerErrorBannerProps) {
  const t = useTranslations('mcp.detail.errorBanner')
  const [expanded, setExpanded] = useState(false)
  const isLong = error.length > SHORT_LIMIT
  const visible = expanded || !isLong ? error : `${error.slice(0, SHORT_LIMIT)}…`

  return (
    <div className="flex gap-3 rounded-lg border-l-4 border-l-destructive bg-destructive/10 p-4 text-sm">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
      <div className="flex min-w-0 flex-col gap-1">
        <span className="font-medium text-destructive">{t('title')}</span>
        <p className="break-words text-destructive/90">{visible}</p>
        {isLong ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 self-start text-xs text-destructive/80 hover:text-destructive"
          >
            {expanded ? (
              <>
                <ChevronUp className="h-3 w-3" />
                {t('collapse')}
              </>
            ) : (
              <>
                <ChevronDown className="h-3 w-3" />
                {t('expand')}
              </>
            )}
          </button>
        ) : null}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/ServerErrorBanner.tsx
git commit -m "feat(mcp): error banner with expandable long-error body"
```

---

## Task 12: ServerHero

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/ServerHero.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/packages/web/components/mcp/detail/ServerHero.tsx
'use client'

import type { MCPServer } from '@cubeplex/core'
import { Loader2, RefreshCw, Share2, Trash2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { MCPScopeBadge } from '../MCPScopeBadge'

export interface ServerHeroProps {
  server: MCPServer
  canRefresh: boolean
  canShare: boolean
  canDelete: boolean
  refreshing: boolean
  deleting: boolean
  onRefresh: () => void
  onShare: () => void
  onDelete: () => void
}

export function ServerHero({
  server,
  canRefresh,
  canShare,
  canDelete,
  refreshing,
  deleting,
  onRefresh,
  onShare,
  onDelete,
}: ServerHeroProps) {
  const t = useTranslations('mcp.detail')
  const connected = server.authed
  const toolsCount = server.tools_cache?.length ?? 0
  const formattedTime = server.last_discovered_at
    ? new Date(server.last_discovered_at).toLocaleString()
    : null
  const toolsLabel = t('toolsCount', { count: toolsCount })
  const metaLine = formattedTime
    ? t('metaLine', { time: formattedTime, tools: toolsLabel })
    : t('metaLineNever', { tools: toolsLabel })

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
              connected
                ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
                : 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300',
            )}
          >
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                connected ? 'bg-emerald-500' : 'bg-rose-500',
              )}
            />
            {connected ? t('statusConnected') : t('statusDisconnected')}
          </span>
          <h1 className="truncate text-2xl font-semibold">{server.name}</h1>
          <MCPScopeBadge scope={server.credential_scope} />
          <span className="rounded-md border border-border px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
            {server.transport}
          </span>
        </div>
        <p className="text-sm text-muted-foreground">{metaLine}</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {canRefresh ? (
          <Button
            type="button"
            variant="default"
            size="sm"
            disabled={refreshing || deleting}
            onClick={onRefresh}
          >
            {refreshing ? (
              <Loader2 data-icon="inline-start" className="animate-spin" />
            ) : (
              <RefreshCw data-icon="inline-start" />
            )}
            {t('refreshTools')}
          </Button>
        ) : null}
        {canShare ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={refreshing || deleting}
            onClick={onShare}
          >
            <Share2 data-icon="inline-start" />
            {t('shareToOrg')}
          </Button>
        ) : null}
        {canDelete ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={refreshing || deleting}
            onClick={onDelete}
            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
          >
            {deleting ? (
              <Loader2 data-icon="inline-start" className="animate-spin" />
            ) : (
              <Trash2 data-icon="inline-start" />
            )}
            {t('delete')}
          </Button>
        ) : null}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/ServerHero.tsx
git commit -m "feat(mcp): server hero with status pill and action cluster"
```

---

## Task 13: OverviewPanel (Connection card + composes Credentials)

**Files:**
- Create: `frontend/packages/web/components/mcp/detail/OverviewPanel.tsx`

- [ ] **Step 1: Create the component**

```tsx
// frontend/packages/web/components/mcp/detail/OverviewPanel.tsx
'use client'

import type { ApiClient, MCPServer } from '@cubeplex/core'
import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'

import { MCPCredentialPanel } from '../MCPCredentialPanel'
import { MCPScopeBadge } from '../MCPScopeBadge'

export interface OverviewPanelProps {
  server: MCPServer
  mode: 'admin' | 'ws-owned' | 'ws-readonly'
  client: ApiClient
  wsId?: string
  onRefresh: () => Promise<void>
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  async function handleCopy(): Promise<void> {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={() => void handleCopy()}
      className="h-7 w-7 p-0"
      aria-label="copy"
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </Button>
  )
}

function Row({
  label,
  children,
  mono,
  copyValue,
}: {
  label: string
  children: React.ReactNode
  mono?: boolean
  copyValue?: string
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border/40 px-4 py-2.5 text-sm last:border-b-0">
      <span className="text-muted-foreground">{label}</span>
      <div className="flex min-w-0 items-center gap-2">
        <span className={cn('truncate text-right', mono ? 'font-mono' : 'font-medium')}>
          {children}
        </span>
        {copyValue ? <CopyButton value={copyValue} /> : null}
      </div>
    </div>
  )
}

export function OverviewPanel({ server, mode, client, wsId, onRefresh }: OverviewPanelProps) {
  const t = useTranslations('mcp.detail.connectionCard')
  const showCredentialPanel = (mode === 'ws-owned' || mode === 'ws-readonly') && wsId

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>{t('title')}</CardTitle>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          <Row label={t('url')} mono copyValue={server.server_url}>
            {server.server_url}
          </Row>
          <Row label={t('transport')}>
            <span className="rounded-md border border-border px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
              {server.transport}
            </span>
          </Row>
          <Row label={t('authMethod')} mono copyValue={server.auth_method}>
            {server.auth_method}
          </Row>
          <Row label={t('scope')}>
            <MCPScopeBadge scope={server.credential_scope} />
          </Row>
          <Row label={t('timeouts')}>
            {t('timeoutsValue', {
              timeout: server.timeout,
              sseTimeout: server.sse_read_timeout,
            })}
          </Row>
        </CardContent>
      </Card>

      {showCredentialPanel && wsId ? (
        <MCPCredentialPanel
          server={server}
          wsId={wsId}
          client={client}
          scopeContext={mode === 'ws-owned' ? 'owned' : 'via-binding'}
          onChange={onRefresh}
        />
      ) : null}
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/mcp/detail/OverviewPanel.tsx
git commit -m "feat(mcp): overview panel with connection card and copy buttons"
```

---

## Task 14: Refactor `MCPServerDetail` to use the new tree

**Files:**
- Modify: `frontend/packages/web/components/mcp/MCPServerDetail.tsx`
- Delete: `frontend/packages/web/components/mcp/MCPToolsTable.tsx`

- [ ] **Step 1: Replace `MCPServerDetail.tsx` with the slim composition**

```tsx
// frontend/packages/web/components/mcp/MCPServerDetail.tsx
'use client'

import { useState } from 'react'
import type { ApiClient, MCPServer } from '@cubeplex/core'
import { useTranslations } from 'next-intl'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { MCPPromoteDialog } from './MCPPromoteDialog'
import { OverviewPanel } from './detail/OverviewPanel'
import { ServerErrorBanner } from './detail/ServerErrorBanner'
import { ServerHero } from './detail/ServerHero'
import { ToolsPanel } from './detail/tools/ToolsPanel'

export interface MCPServerDetailProps {
  server: MCPServer
  mode: 'admin' | 'ws-owned' | 'ws-readonly'
  client: ApiClient
  wsId?: string
  onRefresh: () => Promise<void>
  onDelete?: () => Promise<void>
  onPromote?: (shareCredential: boolean) => Promise<void>
}

export function MCPServerDetail({
  server,
  mode,
  client,
  wsId,
  onRefresh,
  onDelete,
  onPromote,
}: MCPServerDetailProps) {
  const t = useTranslations('mcp.detail')
  const [refreshing, setRefreshing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [promoteOpen, setPromoteOpen] = useState(false)

  const canRefreshTools = mode !== 'ws-readonly'
  const canShare = mode === 'ws-owned' && Boolean(onPromote)
  const canDelete = Boolean(onDelete)
  const tools = server.tools_cache ?? []

  async function handleRefresh(): Promise<void> {
    setRefreshing(true)
    try {
      await onRefresh()
    } finally {
      setRefreshing(false)
    }
  }

  async function handleDelete(): Promise<void> {
    if (!onDelete) return
    setDeleting(true)
    try {
      await onDelete()
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <ServerHero
        server={server}
        canRefresh={canRefreshTools}
        canShare={canShare}
        canDelete={canDelete}
        refreshing={refreshing}
        deleting={deleting}
        onRefresh={() => void handleRefresh()}
        onShare={() => setPromoteOpen(true)}
        onDelete={() => void handleDelete()}
      />

      {server.last_error ? <ServerErrorBanner error={server.last_error} /> : null}

      <Tabs defaultValue="overview">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">{t('overview')}</TabsTrigger>
          <TabsTrigger value="tools">{t('toolsTab', { count: tools.length })}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          <OverviewPanel server={server} mode={mode} client={client} wsId={wsId} onRefresh={onRefresh} />
        </TabsContent>

        <TabsContent value="tools" className="mt-4">
          <ToolsPanel tools={tools} />
        </TabsContent>
      </Tabs>

      {onPromote ? (
        <MCPPromoteDialog
          server={server}
          open={promoteOpen}
          onOpenChange={setPromoteOpen}
          onConfirm={onPromote}
        />
      ) : null}
    </div>
  )
}
```

- [ ] **Step 2: Delete the old tools table**

```bash
rm frontend/packages/web/components/mcp/MCPToolsTable.tsx
```

- [ ] **Step 3: Verify nothing else imports MCPToolsTable**

```bash
grep -rn "MCPToolsTable" frontend/packages
```

Expected: no matches.

- [ ] **Step 4: Type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/mcp/MCPServerDetail.tsx
git add -u frontend/packages/web/components/mcp/MCPToolsTable.tsx
git commit -m "refactor(mcp): rebuild MCPServerDetail with hero + overview + master-detail tools"
```

---

## Task 15: Final verification (lint, type-check, manual smoke)

**Files:**
- (none — verification only)

- [ ] **Step 1: Run full frontend type-check**

```bash
cd frontend
pnpm type-check
```

Expected: no errors.

- [ ] **Step 2: Run frontend lint (if configured)**

```bash
cd frontend
pnpm --filter web lint 2>&1 | tail -20 || true
```

Expected: no new errors over baseline. If `pnpm --filter web lint` is not a script, skip this step.

- [ ] **Step 3: Run backend `make check`**

```bash
cd backend
make check
```

Expected: all green.

- [ ] **Step 4: Manual smoke check**

Start the dev servers for this worktree (ports 8019 / 3019):

```bash
# terminal A
cd backend && python main.py
# terminal B
cd frontend && pnpm dev
```

In a browser at http://localhost:3019, log in as an admin and navigate to `/admin/mcp`. For each scenario, verify:

1. **Connected server with tools** — Hero shows green Connected pill; tool count and "Last synced …" are correct; Refresh repopulates `input_schema` so SchemaView renders real parameter rows (not "No parameters").
2. **Disconnected server** — Hero shows rose Disconnected pill.
3. **Server with `last_error`** — ServerErrorBanner appears between hero and tabs; long errors collapse and expand.
4. **Overview tab** — Connection card shows URL with working copy button; transport and scope chips render; timeouts row uses the new format.
5. **Tools tab — empty state** — Newly added server with no tools shows the "No tools discovered yet — click Refresh" empty state.
6. **Tools tab — populated** — Filter input narrows the list; counts update; clicking a tool selects it (left-border + tint); right panel shows tool name + description + 3-way tab switch.
7. **Schema view** — Pick a tool with required + optional params, enums, defaults: each renders correctly. If available, find a tool with nested objects or oneOf and verify expand/collapse + variant pill.
8. **Try-it view** — Form generates one input per property; Run button is disabled with the "Try-it backend not yet available" tooltip.
9. **JSON view** — Copy button copies the schema; toast/feedback fires.

Note anything that misrenders in this checklist and fix before opening the PR.

- [ ] **Step 5: Final commit (only if fixes were needed)**

```bash
git status
# if there are uncommitted fixes from manual verification:
git add <changed files>
git commit -m "fix(mcp): post-smoke adjustments to MCP detail redesign"
```

- [ ] **Step 6: Verify branch state**

```bash
git log --oneline origin/main..HEAD
```

Expected: ~13–15 commits matching the task structure above. Branch ready for PR.

---

## Out of scope (do not implement here)

- Backend invoke endpoint for Try-it.
- Migration of existing empty `tools_cache` rows (admins re-click Refresh).
- New E2E tests.
- Any change to `/admin/mcp` server-list rendering or to catalog install / promote dialogs.
