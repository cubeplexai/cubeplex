'use client'

import { use, useEffect, useMemo } from 'react'
import { useArtifactStore, useConversationStore } from '@cubebox/core'
import { WorkspaceContext } from '@/hooks/useWorkspaceContext'

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
    // Reset cross-workspace state when the wsId changes so stale conversations
    // and artifacts from the previous workspace don't bleed through.
    useConversationStore.setState({ conversations: [], activeId: null })
    useArtifactStore.setState({ artifacts: {} })
  }, [wsId])

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}
