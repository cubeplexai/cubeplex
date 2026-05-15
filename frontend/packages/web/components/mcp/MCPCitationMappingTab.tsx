'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { AlertTriangle, Check, Circle } from 'lucide-react'
import {
  wsGetToolCitations,
  wsPatchToolCitations,
  type ApiClient,
  type CitationConfigJSON,
  type ToolCitationsResponse,
} from '@cubebox/core'

import { Button } from '@/components/ui/button'
import { getProperties, type SchemaNode } from '@/lib/jsonSchemaTypes'
import { MasterDetailList } from './detail/MasterDetailList'
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

interface ToolItem {
  name: string
  description?: string
  input_schema?: unknown
  hasCitation: boolean
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
        setError(null)
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

  const toolItems = useMemo<ToolItem[]>(
    () =>
      (state?.tools_cache ?? []).map((c) => ({
        name: c.name,
        description: (c as { description?: string }).description,
        input_schema: c.input_schema,
        hasCitation: draft[c.name] !== undefined,
      })),
    [state, draft],
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
    if (!cached?.input_schema || typeof cached.input_schema !== 'object') return []
    return Object.keys(getProperties(cached.input_schema as SchemaNode))
  }, [state, selectedTool])

  if (!state) {
    if (error) {
      return (
        <div
          role="alert"
          className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-12 text-center text-sm text-destructive"
        >
          {error}
        </div>
      )
    }
    return (
      <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center text-sm text-muted-foreground">
        {t('loading')}
      </div>
    )
  }

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
    setError(null)
    setSaving(true)
    try {
      const updated = await wsPatchToolCitations(client, workspaceId, serverId, draft)
      setState(updated)
      setDraft(updated.tool_citations)
      setDirty(false)
      setError(null)
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const orphanFooter =
    orphans.length > 0 ? (
      <div className="mt-2 border-t border-border/60 pt-2">
        {orphans.map((k) => (
          <div key={k} className="flex items-center gap-2 px-3 py-1.5 text-amber-600">
            <AlertTriangle aria-hidden className="h-3.5 w-3.5 shrink-0" />
            <span className="flex-1 truncate font-mono text-sm">{k}</span>
            <Button variant="ghost" size="sm" onClick={() => onChange(k, null)}>
              {t('remove')}
            </Button>
          </div>
        ))}
      </div>
    ) : null

  const selectedToolData = selectedTool
    ? (state.tools_cache.find((c) => c.name === selectedTool) ?? null)
    : null
  const selectedDescription =
    selectedToolData && 'description' in selectedToolData
      ? (selectedToolData as { description?: string }).description
      : undefined

  return (
    <div className="flex flex-col gap-4">
      {error && (
        <div
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/5 px-4 py-2 text-sm text-destructive"
        >
          {error}
        </div>
      )}
      <div className="grid min-h-[420px] grid-cols-[280px_minmax(0,1fr)] gap-6">
        <aside className="min-h-0 border-r border-border/60 pr-4">
          <MasterDetailList<ToolItem>
            items={toolItems}
            getKey={(item) => item.name}
            filter={(item, q) => {
              const lq = q.toLowerCase()
              return (
                item.name.toLowerCase().includes(lq) ||
                (item.description ?? '').toLowerCase().includes(lq)
              )
            }}
            selectedKey={selectedTool}
            onSelect={trySetSelectedTool}
            searchPlaceholder={t('filterPlaceholder')}
            countLabel={(matched, total, q) =>
              q ? t('countMatch', { matched, total }) : t('countAll', { count: total })
            }
            emptyState={t('emptyTools')}
            emptyMatchState={(q) => t('emptyToolsMatch', { query: q })}
            footerSection={orphanFooter}
            renderItem={(item) => (
              <span className="flex items-center gap-2">
                {item.hasCitation ? (
                  <Check aria-hidden className="h-3.5 w-3.5 shrink-0 text-primary" />
                ) : (
                  <Circle aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                )}
                <span className="truncate font-mono text-sm font-semibold">{item.name}</span>
              </span>
            )}
          />
        </aside>

        <section className="flex min-h-0 flex-col gap-4">
          {selectedTool ? (
            <>
              <div className="flex flex-col gap-1">
                <h2 className="font-mono text-lg font-semibold">{selectedTool}</h2>
                {selectedDescription ? (
                  <p className="text-sm text-muted-foreground">{selectedDescription}</p>
                ) : null}
              </div>

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

              {canEdit && (
                <div className="mt-auto flex justify-end pt-4">
                  <Button disabled={!dirty || saving} onClick={() => void save()}>
                    {saving ? t('saving') : t('saveChanges')}
                  </Button>
                </div>
              )}
            </>
          ) : (
            <div className="rounded-lg border border-dashed border-border px-6 py-12 text-center text-sm text-muted-foreground">
              {t('emptyTools')}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
