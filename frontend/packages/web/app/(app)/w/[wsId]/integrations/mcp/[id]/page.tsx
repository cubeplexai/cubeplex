'use client'

import { useEffect, useMemo, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { createApiClient, useWorkspaceMcpStore, wsGetServer } from '@cubebox/core'
import type { MCPServer } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { MCPServerDetail } from '@/components/mcp/MCPServerDetail'

export default function WorkspaceMcpDetailPage() {
  const t = useTranslations('mcp.wsPage')
  const { wsId, id } = useParams<{ wsId: string; id: string }>()
  const router = useRouter()
  const client = useMemo(() => {
    const next = createApiClient('')
    next.setWorkspaceId(wsId)
    return next
  }, [wsId])
  const { remove, refreshTools, promote } = useWorkspaceMcpStore()
  const [server, setServer] = useState<MCPServer | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true

    async function load(): Promise<void> {
      setError(null)
      try {
        const loaded = await wsGetServer(client, wsId, id)
        if (active) setServer(loaded)
      } catch (err) {
        if (active) setError((err as Error).message)
      }
    }

    void load()

    return () => {
      active = false
    }
  }, [client, id, wsId])

  if (error) return <div className="text-sm text-destructive">{error}</div>
  if (!server) return <div className="text-sm text-muted-foreground">{t('loadingConnector')}</div>

  const isOwned = server.owner_workspace_id === wsId

  return (
    <MCPServerDetail
      server={server}
      mode={isOwned ? 'ws-owned' : 'ws-readonly'}
      client={client}
      wsId={wsId}
      onRefresh={async () => {
        if (isOwned) setServer(await refreshTools(client, wsId, id))
      }}
      onDelete={
        isOwned
          ? async () => {
              if (!window.confirm(t('deleteConfirm', { name: server.name }))) return
              await remove(client, wsId, id)
              router.push(`/w/${wsId}/integrations/mcp`)
            }
          : undefined
      }
      onPromote={
        isOwned
          ? async (shareCredential: boolean) => {
              setServer(await promote(client, wsId, id, { share_credential: shareCredential }))
            }
          : undefined
      }
    />
  )
}
