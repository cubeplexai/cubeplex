'use client'

import { useTranslations } from 'next-intl'
import type { CitationConfigJSON } from '@cubeplex/core'
import { Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface Props {
  toolName: string
  outputFieldCandidates: string[]
  value: CitationConfigJSON | null
  onChange: (next: CitationConfigJSON | null) => void
  readOnly?: boolean
}

const DEFAULT_CFG: CitationConfigJSON = {
  content_type: 'json',
  source_type: 'web',
  content_field: null,
  mapping: { snippet: '' },
}

export function MCPCitationEditor({
  toolName,
  outputFieldCandidates,
  value,
  onChange,
  readOnly,
}: Props) {
  const t = useTranslations('mcp.citations')
  const cfg: CitationConfigJSON = value ?? DEFAULT_CFG

  const renameMapping = (oldKey: string, newKey: string): void => {
    if (newKey === oldKey) return
    const m: Record<string, string> = {}
    for (const [k, v] of Object.entries(cfg.mapping)) {
      m[k === oldKey ? newKey : k] = v
    }
    onChange({ ...cfg, mapping: m })
  }

  const updateMappingValue = (key: string, next: string): void => {
    onChange({ ...cfg, mapping: { ...cfg.mapping, [key]: next } })
  }

  const removeMapping = (key: string): void => {
    const m = { ...cfg.mapping }
    delete m[key]
    onChange({ ...cfg, mapping: m })
  }

  const addMapping = (): void => onChange({ ...cfg, mapping: { ...cfg.mapping, '': '' } })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold">{t('editorTitle', { tool: toolName })}</h3>
        {value ? (
          <span className="rounded bg-success-surface px-1.5 py-0.5 text-[10px] font-medium text-success-fg">
            {t('statusMapped')}
          </span>
        ) : (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            {t('statusUnmapped')}
          </span>
        )}
      </div>

      {!readOnly ? (
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => onChange(null)}>
            {t('disable')}
          </Button>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label>{t('sourceType')}</Label>
          <Input
            value={cfg.source_type}
            onChange={(e) => onChange({ ...cfg, source_type: e.target.value })}
            readOnly={readOnly}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>{t('contentType')}</Label>
          <select
            className="h-8 rounded-md border px-2 text-sm"
            value={cfg.content_type}
            onChange={(e) => onChange({ ...cfg, content_type: e.target.value as 'json' | 'text' })}
            disabled={readOnly}
          >
            <option value="json">json</option>
            <option value="text">text</option>
          </select>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label>{t('contentField')}</Label>
        <Input
          list={`citation-output-fields-${toolName}`}
          value={cfg.content_field ?? ''}
          onChange={(e) => onChange({ ...cfg, content_field: e.target.value || null })}
          placeholder={t('contentFieldRoot')}
          readOnly={readOnly}
        />
        {outputFieldCandidates.length > 0 ? (
          <datalist id={`citation-output-fields-${toolName}`}>
            {outputFieldCandidates.map((f) => (
              <option key={f} value={f} />
            ))}
          </datalist>
        ) : null}
      </div>

      <div className="flex flex-col gap-2">
        <Label>{t('mapping')}</Label>
        <div className="flex flex-col gap-2">
          {Object.entries(cfg.mapping).map(([k, v], i) => (
            <div key={`${i}-${k}`} className="flex items-center gap-2">
              <Input
                value={k}
                onChange={(e) => renameMapping(k, e.target.value)}
                placeholder={t('mappingKeyPlaceholder')}
                readOnly={readOnly}
                className="max-w-[180px]"
              />
              <Input
                list={`citation-output-fields-${toolName}`}
                value={v}
                onChange={(e) => updateMappingValue(k, e.target.value)}
                placeholder={t('mappingValuePlaceholder')}
                readOnly={readOnly}
                className="flex-1"
              />
              {!readOnly ? (
                <button
                  type="button"
                  onClick={() => removeMapping(k)}
                  aria-label={t('removeMapping')}
                  className="text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="size-3.5" />
                </button>
              ) : null}
            </div>
          ))}
        </div>
        {!readOnly ? (
          <Button variant="outline" size="sm" onClick={addMapping}>
            {t('addMapping')}
          </Button>
        ) : null}
      </div>
    </div>
  )
}
