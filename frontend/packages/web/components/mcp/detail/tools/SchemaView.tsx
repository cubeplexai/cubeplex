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

  const properties = getProperties(schema)
  const required = new Set(getRequired(schema))
  const entries = Object.entries(properties)

  // Empty object / no properties: treat as a zero-arg tool, not malformed.
  // This covers both `{}` and `{type: 'object', properties: {}}`.
  if (entries.length === 0) {
    return <p className="px-4 py-6 text-sm text-muted-foreground">{t('noParams')}</p>
  }

  if (!isObjectSchema(schema)) {
    return <p className="px-4 py-6 text-sm text-muted-foreground">{t('malformed')}</p>
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
