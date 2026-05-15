'use client'

import { useTranslations } from 'next-intl'
import type { CitationConfigJSON } from '@cubebox/core'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { MCPCitationFieldRow } from './MCPCitationFieldRow'

export interface PeerMapping {
  serverId: string
  serverName: string
  config: CitationConfigJSON
}

interface Props {
  toolName: string
  inputSchemaArgs: string[]
  outputFieldCandidates: string[] | null
  value: CitationConfigJSON | null
  defaultFromCatalog: CitationConfigJSON | null
  peerMappings: PeerMapping[]
  onChange: (next: CitationConfigJSON | null) => void
  onCopyFromPeer?: (cfg: CitationConfigJSON) => void
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
  inputSchemaArgs: _inputSchemaArgs,
  outputFieldCandidates,
  value,
  defaultFromCatalog,
  peerMappings,
  onChange,
  onCopyFromPeer,
  readOnly,
}: Props) {
  const t = useTranslations('mcp.serverDetail.citations')
  const cfg = value ?? DEFAULT_CFG

  const renameMapping = (oldKey: string, newKey: string) => {
    if (newKey === oldKey) return
    const m: Record<string, string> = {}
    for (const [k, v] of Object.entries(cfg.mapping)) {
      m[k === oldKey ? newKey : k] = v
    }
    onChange({ ...cfg, mapping: m })
  }

  const updateMappingValue = (key: string, next: string) => {
    onChange({ ...cfg, mapping: { ...cfg.mapping, [key]: next } })
  }

  const removeMapping = (key: string) => {
    const m = { ...cfg.mapping }
    delete m[key]
    onChange({ ...cfg, mapping: m })
  }

  const addMapping = () => onChange({ ...cfg, mapping: { ...cfg.mapping, '': '' } })

  return (
    <div className="space-y-4">
      {!readOnly && (
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => onChange(null)}>
            {t('disable')}
          </Button>
          {defaultFromCatalog && (
            <Button variant="outline" size="sm" onClick={() => onChange({ ...defaultFromCatalog })}>
              {t('resetToCatalogDefault')}
            </Button>
          )}
          {peerMappings.length > 0 && onCopyFromPeer && (
            <select
              className="rounded-md border px-2 py-1 text-sm"
              defaultValue=""
              aria-label={t('copyFromPeer')}
              onChange={(e) => {
                const peer = peerMappings.find((p) => p.serverId === e.target.value)
                if (peer) onCopyFromPeer(peer.config)
                e.currentTarget.value = ''
              }}
            >
              <option value="">{t('copyFromPeer')}</option>
              {peerMappings.map((p) => (
                <option key={p.serverId} value={p.serverId}>
                  {p.serverName}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label>{t('sourceType')}</Label>
          <Input
            value={cfg.source_type}
            onChange={(e) => onChange({ ...cfg, source_type: e.target.value })}
            readOnly={readOnly}
          />
        </div>
        <div>
          <Label>{t('contentType')}</Label>
          <select
            className="w-full rounded-md border px-2 py-1"
            value={cfg.content_type}
            onChange={(e) => onChange({ ...cfg, content_type: e.target.value as 'json' | 'text' })}
            disabled={readOnly}
          >
            <option value="json">json</option>
            <option value="text">text</option>
          </select>
        </div>
      </div>

      <div>
        <Label>{t('resultLocation')}</Label>
        <div className="mt-1 flex items-center gap-2">
          <input
            type="checkbox"
            checked={cfg.content_field === null}
            onChange={(e) => onChange({ ...cfg, content_field: e.target.checked ? null : '' })}
            disabled={readOnly}
            id={`whole-response-${toolName}`}
          />
          <label htmlFor={`whole-response-${toolName}`} className="text-sm">
            {t('wholeResponseIsOneItem')}
          </label>
        </div>
        {cfg.content_field !== null && (
          <Input
            className="mt-2"
            value={cfg.content_field}
            placeholder={t('contentFieldPlaceholder')}
            onChange={(e) => onChange({ ...cfg, content_field: e.target.value })}
            readOnly={readOnly}
          />
        )}
      </div>

      <div>
        <Label>{t('metadataMapping')}</Label>
        <div className="mt-2 space-y-2">
          {Object.entries(cfg.mapping).map(([meta, out], idx) => (
            <MCPCitationFieldRow
              key={meta || `empty-row-${idx}`}
              metaField={meta}
              outputField={out}
              outputFieldCandidates={outputFieldCandidates}
              onMetaFieldChange={(v) => renameMapping(meta, v)}
              onOutputFieldChange={(v) => updateMappingValue(meta, v)}
              onRemove={() => removeMapping(meta)}
              readOnly={readOnly}
            />
          ))}
          {!readOnly && (
            <Button variant="ghost" size="sm" onClick={addMapping}>
              + {t('addField')}
            </Button>
          )}
        </div>
        {outputFieldCandidates === null && (
          <p className="mt-1 text-xs text-muted-foreground">{t('noSampleHint')}</p>
        )}
      </div>
    </div>
  )
}
