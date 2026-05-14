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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { getProperties, getRequired, resolveType, type SchemaNode } from '@/lib/jsonSchemaTypes'

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
            <TooltipTrigger
              tabIndex={-1}
              aria-hidden
              className="cursor-default border-0 bg-transparent p-0"
            >
              <span tabIndex={0} className="inline-flex">
                <Button type="button" disabled tabIndex={-1}>
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
          onValueChange={(v) => onChange(v ?? '')}
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
