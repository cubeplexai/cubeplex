'use client'

import { use, useEffect, useMemo } from 'react'
import {
  createApiClient,
  useConversationStore,
  useMcpToolRegistryStore,
  useTopicStore,
} from '@cubeplex/core'
import { WorkspaceContext } from '@/hooks/useWorkspaceContext'
import { resetWorkspaceScopedClientState } from '@/lib/resetWorkspaceScopedState'

export default function WorkspaceLayout({
  params,
  children,
}: {
  params: Promise<{ wsId: string }>
  children: React.ReactNode
}) {
  const { wsId } = use(params)
  const value = useMemo(() => ({ workspaceId: wsId }), [wsId])

  useEffect(() => {
    // Reset cross-workspace state when the wsId changes so stale conversations,
    // artifacts, and the right-hand panel from the previous workspace don't
    // bleed through, then load the new workspace's conversation list so the
    // sidebar is populated on every page within the workspace.
    resetWorkspaceScopedClientState()
    const client = createApiClient('')
    client.setWorkspaceId(wsId)
    useConversationStore.getState().fetchList(client)
    useTopicStore.getState().fetchList(client)
    useMcpToolRegistryStore.getState().loadForWorkspace(client, wsId)
  }, [wsId])

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}
