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

function resolveNode(
  node: SchemaNode,
  root: SchemaNode,
): {
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

  const effective: SchemaNode = variants ? (variants[activeVariant] ?? resolved) : resolved
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

        {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}

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
          {hasNestedObject ? renderNestedObject(effective, root, depth + 1) : null}
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
