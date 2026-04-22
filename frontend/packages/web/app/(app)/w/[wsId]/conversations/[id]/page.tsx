'use client'

import { use, useEffect, useMemo, useState } from 'react'
import {
  createApiClient,
  useArtifactStore,
  useConversationStore,
  usePanelStore,
} from '@cubebox/core'
import { AppShell } from '@/components/layout/AppShell'
import { MessageList } from '@/components/chat/MessageList'
import { ArtifactGallery } from '@/components/chat/ArtifactGallery'
import { InputBar } from '@/components/layout/InputBar'
import { ErrorState } from '@/components/shared/ErrorState'

export default function ChatPage({ params }: { params: Promise<{ wsId: string; id: string }> }) {
  const { wsId, id: conversationId } = use(params)
  const setActive = useConversationStore((s) => s.setActive)
  const fetchList = useConversationStore((s) => s.fetchList)
  const conversations = useConversationStore((s) => s.conversations)
  const loadArtifacts = useArtifactStore((s) => s.loadArtifacts)
  const [status, setStatus] = useState<'loading' | 'ok' | 'notfound' | 'forbidden'>('loading')

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    usePanelStore.getState().close()
    setActive(conversationId)
    ;(async () => {
      const res = await client.get(`/api/v1/conversations/${conversationId}`)
      if (res.status === 404) setStatus('notfound')
      else if (res.status === 403) setStatus('forbidden')
      else if (res.ok) setStatus('ok')
      else setStatus('notfound')
    })()
    fetchList(client)
    loadArtifacts(client, conversationId)
  }, [conversationId, client, setActive, fetchList, loadArtifacts])

  if (status === 'notfound') {
    return (
      <ErrorState
        title="Conversation not found"
        description="It may have been deleted, or it belongs to a different workspace."
        backHref={`/w/${wsId}`}
        backLabel="Back to workspace"
      />
    )
  }
  if (status === 'forbidden') {
    return (
      <ErrorState
        title="No access"
        description="You are not a member of this workspace."
        backHref="/workspaces"
        backLabel="Choose a workspace"
      />
    )
  }

  const currentConvo = conversations.find((c) => c.id === conversationId)

  return (
    <AppShell headerTitle={currentConvo?.title}>
      <ArtifactGallery conversationId={conversationId} />
      <MessageList conversationId={conversationId} />
      <div className="border-t border-border px-4 py-3 bg-background">
        <InputBar conversationId={conversationId} />
      </div>
    </AppShell>
  )
}
