'use client'

import { useEffect, useMemo, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { adminGetServer, createApiClient, useMcpStore, useWorkspaceStore } from '@cubebox/core'
import type { MCPServer } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { MCPServerDetail } from '@/components/mcp/MCPServerDetail'

export default function AdminMcpDetailPage() {
  const t = useTranslations('mcp.adminPage')
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const client = useMemo(() => createApiClient(''), [])
  const { refreshTools, remove } = useMcpStore()
  const { workspaces, fetchList: fetchWorkspaces } = useWorkspaceStore()
  const [server, setServer] = useState<MCPServer | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true

    async function load(): Promise<void> {
      setError(null)
      try {
        const [loaded] = await Promise.all([
          adminGetServer(client, params.id),
          fetchWorkspaces(client),
        ])
        if (active) setServer(loaded)
      } catch (err) {
        if (active) setError((err as Error).message)
      }
    }

    void load()

    return () => {
      active = false
    }
  }, [client, fetchWorkspaces, params.id])

  if (error) return <div className="text-sm text-destructive">{error}</div>
  if (!server) return <div className="text-sm text-muted-foreground">{t('loadingConnector')}</div>

  return (
    <MCPServerDetail
      server={server}
      mode="admin"
      client={client}
      workspaces={workspaces.map((workspace) => ({ id: workspace.id, name: workspace.name }))}
      onRefresh={async () => {
        setServer(await refreshTools(client, server.id))
      }}
      onDelete={async () => {
        if (!window.confirm(t('deleteConfirm', { name: server.name }))) {
          return
        }
        await remove(client, server.id)
        router.push('/admin/mcp')
      }}
    />
  )
}
