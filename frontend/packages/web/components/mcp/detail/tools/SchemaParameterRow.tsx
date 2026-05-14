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
    </div>
  )
}
