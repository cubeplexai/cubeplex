'use client'

import { use, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import {
  createApiClient,
  type Conversation,
  useArtifactStore,
  useConversationStore,
  usePanelStore,
} from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { getPresetSelectionStore } from '@/lib/stores/preset-selection'
import type { ThinkingLevel } from '@/lib/types/presets'

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
  const focusArtifactId = useSearchParams().get('artifact')
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
      else if (res.ok) {
        setStatus('ok')
        // Sync the composer to this conversation's stored model setting, once
        // per open/switch (the effect is keyed by conversationId). For a NEW
        // chat the home page handles the first send before this route mounts,
        // so the persisted last-used value stays the default there.
        try {
          const convo = (await res.json()) as Conversation
          const store = getPresetSelectionStore(wsId).getState()
          store.setModelKey(convo.model_key ?? null)
          store.setThinking((convo.thinking ?? 'medium') as ThinkingLevel)
        } catch {
          // Best-effort: a malformed body just leaves the persisted default.
        }
      } else setStatus('notfound')
    })()
    loadArtifacts(client, conversationId)
  }, [conversationId, client, wsId, setActive, loadArtifacts])

  // Arriving from the artifacts library with `?artifact=<id>` auto-opens that
  // artifact's preview. Runs after the reset effect above (declaration order),
  // so it isn't clobbered by its close(); the panel fills in once loadArtifacts
  // populates the store.
  useEffect(() => {
    if (focusArtifactId) {
      usePanelStore.getState().openArtifact(conversationId, focusArtifactId)
    }
  }, [conversationId, focusArtifactId])

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
