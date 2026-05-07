'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { createApiClient, useWorkspaceSettingsStore } from '@cubebox/core'
import type { MCPServerItem } from '@cubebox/core'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface McpPanelProps {
  wsId: string
}

export function McpPanel({ wsId }: McpPanelProps) {
  const { mcp, loading, loadAll, toggleMCP } = useWorkspaceSettingsStore()
  const [selected, setSelected] = useState<MCPServerItem | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)

  const client = useCallback(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!mcp) loadAll(client())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsId])

  const handleToggle = async (srv: MCPServerItem, enabled: boolean) => {
    if (srv.scope === 'workspace') return
    setToggling(srv.server_id)
    try {
      await toggleMCP(client(), srv.server_id, enabled)
    } finally {
      setToggling(null)
    }
  }

  const renderSection = (title: string, servers: MCPServerItem[]) => (
    <div className="mb-2">
      <p className="px-2 text-[9px] font-semibold uppercase tracking-widest text-muted-foreground/50 mb-1">
        {title}
      </p>
      {servers.length === 0 ? (
        <p className="text-xs text-muted-foreground px-2 py-2">None</p>
      ) : (
        servers.map((srv) => (
          <button
            key={srv.server_id}
            onClick={() => setSelected(srv)}
            className={cn(
              'w-full flex items-center gap-2 px-2 py-2 rounded-md text-left transition-colors',
              selected?.server_id === srv.server_id
                ? 'bg-primary/10 text-primary'
                : 'text-muted-foreground hover:text-foreground hover:bg-accent/60',
            )}
          >
            <div className="flex-1 min-w-0">
              <p className="text-[12px] font-medium truncate">{srv.name}</p>
              <p className="text-[10px] text-muted-foreground/60 truncate">{srv.server_url}</p>
            </div>
            <Switch
              checked={srv.enabled}
              disabled={srv.scope === 'workspace' || toggling === srv.server_id}
              onCheckedChange={(v) => handleToggle(srv, v)}
              onClick={(e) => e.stopPropagation()}
              className="shrink-0 scale-75"
            />
          </button>
        ))
      )}
    </div>
  )

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Col 2: list */}
      <div className="w-56 shrink-0 border-r border-border overflow-y-auto">
        <div className="p-3 border-b border-border flex items-center justify-between gap-2">
          <p className="text-sm font-semibold">MCP Connectors</p>
          <Link
            href={`/w/${wsId}/integrations/mcp/new`}
            className="text-[11px] text-muted-foreground hover:text-foreground shrink-0"
          >
            + Add
          </Link>
        </div>
        <div className="p-2">
          {loading && !mcp ? (
            <p className="text-xs text-muted-foreground py-4 px-2">Loading…</p>
          ) : (
            <>
              {renderSection('Org-wide', mcp?.org_servers ?? [])}
              {renderSection('Workspace private', mcp?.workspace_servers ?? [])}
            </>
          )}
        </div>
      </div>

      {/* Col 3: detail */}
      <div className="flex-1 overflow-y-auto p-8">
        {selected ? (
          <>
            <h2 className="text-base font-semibold mb-1">{selected.name}</h2>
            <div className="flex gap-2 mb-6">
              <Badge variant="outline">{selected.transport}</Badge>
              <Badge variant={selected.scope === 'workspace' ? 'secondary' : 'outline'}>
                {selected.scope === 'workspace' ? 'workspace-private' : 'org-wide'}
              </Badge>
              <Badge variant={selected.enabled ? 'default' : 'secondary'}>
                {selected.enabled ? 'enabled' : 'disabled'}
              </Badge>
            </div>
            <div className="space-y-3 text-sm text-muted-foreground">
              <div className="flex justify-between py-2 border-b border-border">
                <span>URL</span>
                <span className="font-mono text-xs truncate max-w-xs">{selected.server_url}</span>
              </div>
              <div className="flex justify-between py-2 border-b border-border">
                <span>Transport</span>
                <span>{selected.transport}</span>
              </div>
              <div className="flex justify-between py-2 border-b border-border">
                <span>Scope</span>
                <span>{selected.scope}</span>
              </div>
              <div className="flex justify-between py-2 border-b border-border">
                <span>Enabled</span>
                <span>{selected.enabled ? 'Yes' : 'No'}</span>
              </div>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Select a connector to view details</p>
        )}
      </div>
    </div>
  )
}
