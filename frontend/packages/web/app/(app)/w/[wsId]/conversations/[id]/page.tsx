'use client'

import { use, useEffect, useMemo, useState } from 'react'
import {
  createApiClient,
  useArtifactStore,
  useConversationStore,
  usePanelStore,
} from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { AppShell } from '@/components/layout/AppShell'
import { MessageList } from '@/components/chat/MessageList'
import { ArtifactGallery } from '@/components/chat/ArtifactGallery'
import { InputBar } from '@/components/layout/InputBar'
import { ErrorState } from '@/components/shared/ErrorState'

export default function ChatPage({ params }: { params: Promise<{ wsId: string; id: string }> }) {
  const t = useTranslations('conversationPage')
  const { wsId, id: conversationId } = use(params)
  const setActive = useConversationStore((s) => s.setActive)
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
    loadArtifacts(client, conversationId)
  }, [conversationId, client, setActive, loadArtifacts])

  if (status === 'notfound') {
    return (
      <ErrorState
        title={t('notFoundTitle')}
        description={t('notFoundBody')}
        backHref={`/w/${wsId}`}
        backLabel={t('notFoundBack')}
      />
    )
  }
  if (status === 'forbidden') {
    return (
      <ErrorState
        title={t('noAccessTitle')}
        description={t('noAccessBody')}
        backHref="/workspaces"
        backLabel={t('noAccessBack')}
      />
    )
  }

  const currentConvo = conversations.find((c) => c.id === conversationId)

  return (
    <AppShell headerTitle={currentConvo?.title} conversationId={conversationId}>
      <ArtifactGallery conversationId={conversationId} />
      <MessageList conversationId={conversationId} />
      <div className="border-t border-border px-4 py-3 bg-background">
        <InputBar conversationId={conversationId} />
      </div>
    </AppShell>
  )
}
