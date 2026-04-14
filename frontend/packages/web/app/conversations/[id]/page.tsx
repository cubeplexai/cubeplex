'use client'

import { useParams } from 'next/navigation'
import { useEffect } from 'react'
import {
  useConversationStore, usePanelStore, useArtifactStore, createApiClient,
} from '@cubebox/core'
import { AppShell } from '@/components/layout/AppShell'
import { MessageList } from '@/components/chat/MessageList'
import { ArtifactGallery } from '@/components/chat/ArtifactGallery'
import { InputBar } from '@/components/layout/InputBar'

export default function ChatPage() {
  const params = useParams()
  const conversationId = params.id as string
  const setActive = useConversationStore((s) => s.setActive)
  const fetchList = useConversationStore((s) => s.fetchList)
  const conversations = useConversationStore((s) => s.conversations)
  const loadArtifacts = useArtifactStore((s) => s.loadArtifacts)

  useEffect(() => {
    usePanelStore.getState().close()
    setActive(conversationId)
    const client = createApiClient('')
    fetchList(client)
    loadArtifacts(client, conversationId)
  }, [conversationId, setActive, fetchList, loadArtifacts])

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
