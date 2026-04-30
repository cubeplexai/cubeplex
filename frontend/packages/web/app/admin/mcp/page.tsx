'use client'

import { useEffect, useMemo } from 'react'
import Link from 'next/link'
import { createApiClient, useMcpStore } from '@cubebox/core'
import { buttonVariants } from '@/components/ui/button'
import { MCPServerList } from '@/components/mcp/MCPServerList'

export default function AdminMcpPage() {
  const client = useMemo(() => createApiClient(''), [])
  const { servers, loading, error, fetchAll } = useMcpStore()

  useEffect(() => {
    void fetchAll(client)
  }, [client, fetchAll])

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold">MCP connectors</h1>
          <p className="text-sm text-muted-foreground">
            Manage organization-level MCP servers and credential scopes.
          </p>
        </div>
        <Link href="/admin/mcp/new" className={buttonVariants({ variant: 'default' })}>
          Add server
        </Link>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      <MCPServerList
        servers={servers}
        loading={loading}
        detailHrefBase="/admin/mcp"
        emptyTitle="No MCP connectors yet"
        emptyDescription="Add a server to expose external MCP tools to Cubebox agents."
      />
    </div>
  )
}
