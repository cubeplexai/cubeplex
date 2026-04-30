'use client'

import { useEffect, useMemo } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { createApiClient, useWorkspaceMcpStore } from '@cubebox/core'
import { buttonVariants } from '@/components/ui/button'
import { MCPServerList } from '@/components/mcp/MCPServerList'

export default function WorkspaceMcpListPage() {
  const { wsId } = useParams<{ wsId: string }>()
  const client = useMemo(() => {
    const next = createApiClient('')
    next.setWorkspaceId(wsId)
    return next
  }, [wsId])
  const { owned, viaBinding, loading, error, fetchAll } = useWorkspaceMcpStore()

  useEffect(() => {
    void fetchAll(client, wsId)
  }, [client, fetchAll, wsId])

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold">Workspace MCP connectors</h1>
          <p className="text-sm text-muted-foreground">
            Manage private connectors and credentials for this workspace.
          </p>
        </div>
        <Link
          href={`/w/${wsId}/integrations/mcp/new`}
          className={buttonVariants({ variant: 'default' })}
        >
          Add server
        </Link>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-medium">Workspace private</h2>
        <MCPServerList
          servers={owned}
          loading={loading}
          detailHrefBase={`/w/${wsId}/integrations/mcp`}
          emptyTitle="No private MCP servers"
          emptyDescription="Add a connector that is visible only inside this workspace."
        />
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-medium">Shared from organization</h2>
        <MCPServerList
          servers={viaBinding}
          loading={loading}
          detailHrefBase={`/w/${wsId}/integrations/mcp`}
          emptyTitle="No organization connectors shared here"
          emptyDescription="Organization admins can bind org-level MCP servers to this workspace."
        />
      </section>
    </div>
  )
}
