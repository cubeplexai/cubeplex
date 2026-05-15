'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'
import {
  getProperties,
  getRequired,
  isObjectSchema,
  resolveRef,
  type SchemaNode,
} from '@/lib/jsonSchemaTypes'

import { SchemaParameterRow } from './SchemaParameterRow'

export interface SchemaViewProps {
  schema: SchemaNode | null | undefined
}

function resolveOnce(node: SchemaNode, root: SchemaNode, visited: Set<string>): SchemaNode {
  if (typeof node.$ref !== 'string') return node
  if (visited.has(node.$ref)) return node
  const target = resolveRef(root, node.$ref)
  if (!target) return node
  visited.add(node.$ref)
  return target
}

export function SchemaView({ schema }: SchemaViewProps) {
  const t = useTranslations('mcp.tools.detail.schema')
  const [activeVariant, setActiveVariant] = useState(0)

  if (!schema || typeof schema !== 'object') {
    return <p className="px-4 py-6 text-sm text-muted-foreground">{t('noParams')}</p>
  }

  // 1. Resolve a top-level $ref wrapper (`{ $ref: "#/$defs/Input", $defs: {...} }`).
  const topVisited = new Set<string>()
  const topResolved = resolveOnce(schema, schema, topVisited)

  // 2. Surface a top-level oneOf/anyOf as a variant switcher.
  const variants: SchemaNode[] | null = Array.isArray(topResolved.oneOf)
    ? (topResolved.oneOf as SchemaNode[])
    : Array.isArray(topResolved.anyOf)
      ? (topResolved.anyOf as SchemaNode[])
      : null
  const variantRaw: SchemaNode = variants ? (variants[activeVariant] ?? topResolved) : topResolved
  const effective = resolveOnce(variantRaw, schema, topVisited)

  const properties = getProperties(effective)
  const required = new Set(getRequired(effective))
  const entries = Object.entries(properties)

  if (entries.length === 0) {
    return (
      <p className="px-4 py-6 text-sm text-muted-foreground">
        {isObjectSchema(effective) ? t('noParams') : t('malformed')}
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {variants ? (
        <div className="flex flex-wrap items-center gap-1">
          {variants.map((_, i) => (
            <button
              key={`top-variant-${i}`}
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
              visitedRefs={topVisited}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
