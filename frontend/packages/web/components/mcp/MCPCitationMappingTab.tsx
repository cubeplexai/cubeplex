'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  wsGetToolCitations,
  wsPatchToolCitations,
  type ApiClient,
  type CitationConfigJSON,
  type ToolCitationsResponse,
} from '@cubebox/core'

import { Button } from '@/components/ui/button'

import { MCPCitationEditor, type PeerMapping } from './MCPCitationEditor'

interface Props {
  workspaceId: string
  serverId: string
  client: ApiClient
  canEdit: boolean
  peerSources?: Array<{
    serverId: string
    serverName: string
    tool_citations: Record<string, CitationConfigJSON>
  }>
}

export function MCPCitationMappingTab({
  workspaceId,
  serverId,
  client,
  canEdit,
  peerSources = [],
}: Props) {
  const t = useTranslations('mcp.serverDetail.citations')
  const [state, setState] = useState<ToolCitationsResponse | null>(null)
  const [draft, setDraft] = useState<Record<string, CitationConfigJSON>>({})
  const [selectedTool, setSelectedTool] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    wsGetToolCitations(client, workspaceId, serverId)
      .then((r) => {
        if (cancelled) return
        setState(r)
        setDraft(r.tool_citations)
        setSelectedTool(r.tools_cache[0]?.name ?? null)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e))
      })
    return () => {
      cancelled = true
    }
  }, [client, workspaceId, serverId])

  useEffect(() => {
    if (!dirty) return
    const h = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [dirty])

  const knownToolNames = useMemo(
    () => new Set((state?.tools_cache ?? []).map((c) => c.name)),
    [state],
  )

  const orphans = useMemo(
    () => Object.keys(draft).filter((k) => !knownToolNames.has(k)),
    [draft, knownToolNames],
  )

  const peerMappingsForSelected: PeerMapping[] = useMemo(() => {
    if (!selectedTool) return []
    return peerSources
      .filter((p) => p.serverId !== serverId)
      .map((p) => {
        const cfg = p.tool_citations?.[selectedTool]
        return cfg ? { serverId: p.serverId, serverName: p.serverName, config: cfg } : null
      })
      .filter((x): x is PeerMapping => x !== null)
  }, [peerSources, serverId, selectedTool])

  const inputSchemaArgs = useMemo(() => {
    if (!selectedTool || !state) return []
    const cached = state.tools_cache.find((c) => c.name === selectedTool)
    const schema = cached?.input_schema as { properties?: Record<string, unknown> } | undefined
    return schema?.properties ? Object.keys(schema.properties) : []
  }, [state, selectedTool])

  if (error) return <div className="text-destructive">{error}</div>
  if (!state) return <div>{t('loading')}</div>

  const trySetSelectedTool = (next: string) => {
    if (dirty && !window.confirm(t('unsavedChanges'))) return
    setDirty(false)
    setDraft(state.tool_citations)
    setSelectedTool(next)
  }

  const onChange = (toolName: string, next: CitationConfigJSON | null) => {
    setDraft((prev) => {
      const copy = { ...prev }
      if (next === null) delete copy[toolName]
      else copy[toolName] = next
      return copy
    })
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      const updated = await wsPatchToolCitations(client, workspaceId, serverId, draft)
      setState(updated)
      setDraft(updated.tool_citations)
      setDirty(false)
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex gap-6">
      <aside className="w-1/3 space-y-1 border-r pr-4">
        {state.tools_cache.map((c) => {
          const has = draft[c.name] !== undefined
          return (
            <button
              key={c.name}
              type="button"
              className={`w-full rounded px-2 py-1 text-left ${selectedTool === c.name ? 'bg-accent' : ''}`}
              onClick={() => trySetSelectedTool(c.name)}
            >
              <span className="mr-2">{has ? '✓' : '○'}</span>
              {c.name}
            </button>
          )
        })}
        {orphans.map((k) => (
          <div key={k} className="flex items-center gap-2 px-2 py-1 text-amber-600">
            <span>⚠</span>
            <span className="flex-1">{k}</span>
            <Button variant="ghost" size="sm" onClick={() => onChange(k, null)}>
              {t('remove')}
            </Button>
          </div>
        ))}
      </aside>

      <section className="flex-1">
        {selectedTool && (
          <MCPCitationEditor
            toolName={selectedTool}
            inputSchemaArgs={inputSchemaArgs}
            outputFieldCandidates={null}
            value={draft[selectedTool] ?? null}
            defaultFromCatalog={state.catalog_defaults?.[selectedTool] ?? null}
            peerMappings={peerMappingsForSelected}
            onChange={(next) => onChange(selectedTool, next)}
            onCopyFromPeer={(cfg) => onChange(selectedTool, cfg)}
            readOnly={!canEdit}
          />
        )}

        {canEdit && (
          <div className="mt-6 flex justify-end">
            <Button disabled={!dirty || saving} onClick={() => void save()}>
              {saving ? t('saving') : t('saveChanges')}
            </Button>
          </div>
        )}
      </section>
    </div>
  )
}
