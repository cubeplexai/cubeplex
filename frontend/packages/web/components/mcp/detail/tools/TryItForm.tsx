'use client'

import { useMemo, useState, type ReactNode } from 'react'
import { Play } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { ToolInvokeResult } from '@cubeplex/core'

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
import { getProperties, getRequired, resolveType, type SchemaNode } from '@/lib/jsonSchemaTypes'

export interface TryItFormProps {
  toolName: string
  inputSchema: Record<string, unknown> | null
  onRun: (args: Record<string, unknown>) => Promise<ToolInvokeResult>
  runDisabled?: boolean
  runDisabledReason?: string
  /** Extra UI rendered above the Run button (used by the admin picker). */
  prefix?: ReactNode
}

type FieldValue = string | number | boolean

function coerceArgs(
  values: Record<string, FieldValue>,
  properties: Record<string, SchemaNode>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(values)) {
    if (value === undefined || value === '') continue
    const node = properties[name]
    const type = node ? resolveType(node).kind : 'string'
    if (type === 'object' || type === 'array') {
      try {
        out[name] = JSON.parse(String(value))
      } catch {
        out[name] = value
      }
    } else if (type === 'integer' || type === 'number') {
      const n = typeof value === 'number' ? value : Number(value)
      out[name] = Number.isFinite(n) ? n : value
    } else if (type === 'boolean') {
      out[name] = Boolean(value)
    } else {
      out[name] = value
    }
  }
  return out
}

export function TryItForm({
  toolName,
  inputSchema,
  onRun,
  runDisabled: runDisabledProp,
  runDisabledReason,
  prefix,
}: TryItFormProps) {
  const t = useTranslations('mcp.tools.detail.tryit')
  const schema = inputSchema as SchemaNode | null
  const properties = useMemo(
    () => (schema && typeof schema === 'object' ? getProperties(schema) : {}),
    [schema],
  )
  const required = useMemo(
    () => new Set(schema && typeof schema === 'object' ? getRequired(schema) : []),
    [schema],
  )

  const [values, setValues] = useState<Record<string, FieldValue>>({})
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<ToolInvokeResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  function setValue(name: string, v: FieldValue): void {
    setValues((prev) => ({ ...prev, [name]: v }))
  }

  async function handleRun(): Promise<void> {
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      const args = coerceArgs(values, properties)
      const res = await onRun(args)
      setResult(res)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setRunning(false)
    }
  }

  const entries = Object.entries(properties)
  const runDisabled = running || runDisabledProp === true

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border border-dashed border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
        {t('banner')}
      </div>

      {prefix}

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

      <div className="flex flex-col items-end gap-1">
        <Button type="button" disabled={runDisabled} onClick={() => void handleRun()}>
          <Play data-icon="inline-start" />
          {running ? t('running') : t('run')}
        </Button>
        {runDisabledProp && runDisabledReason ? (
          <p className="text-xs text-muted-foreground">{runDisabledReason}</p>
        ) : null}
      </div>

      {error ? <p className="text-xs text-destructive">{error}</p> : null}

      {result ? (
        <div className="overflow-hidden rounded-lg border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border/60 bg-muted/40 px-3 py-2">
            <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
              {result.ok ? t('resultOk') : t('resultErr')} · {result.duration_ms}ms
            </span>
          </div>
          <pre className="max-h-80 overflow-auto p-4 font-mono text-xs leading-relaxed">
            {result.ok ? JSON.stringify(result.result ?? null, null, 2) : (result.error ?? '')}
          </pre>
        </div>
      ) : null}
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
          // Empty input → pass '' so coerceArgs skips this field
          // (optional numeric arg cleared by user). Number('') === 0
          // would otherwise invoke the tool with 0 for an unset arg.
          onChange={(e) => {
            const raw = e.target.value
            if (raw === '') {
              onChange('')
            } else {
              const n = Number(raw)
              onChange(Number.isFinite(n) ? n : raw)
            }
          }}
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
