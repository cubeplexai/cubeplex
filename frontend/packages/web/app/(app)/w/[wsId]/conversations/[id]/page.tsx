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

import {
  consumeLocallyCreatedConversation,
  getPresetSelectionStore,
} from '@/lib/stores/preset-selection'
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
  // The conversation whose stored model selection has been synced into the
  // composer. Derived `modelSyncPending` blocks new-turn sends until the open
  // conversation's setting has loaded, so a send right after a conversation
  // switch can't ship the previous conversation's model. Tracked as an id (set
  // only in the async callback) rather than a boolean toggled in the effect
  // body, which would be a synchronous setState-in-effect.
  const [syncedConvId, setSyncedConvId] = useState<string | null>(null)
  const modelSyncPending = syncedConvId !== conversationId

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    usePanelStore.getState().close()
    setActive(conversationId)
    // Snapshot the composer selection at open time. A late response (the user
    // switched conversations before this fetch resolved) is dropped via
    // `cancelled`; an edit the user made while the fetch was in flight is
    // respected by the `before` equality check below — neither gets clobbered
    // by this conversation's stored setting.
    let cancelled = false
    const opened = getPresetSelectionStore(wsId).getState()
    const before = { modelKey: opened.modelKey, thinking: opened.thinking }
    ;(async () => {
      const res = await client.get(`/api/v1/conversations/${conversationId}`)
      if (cancelled) return
      if (res.status === 404) setStatus('notfound')
      else if (res.status === 403) setStatus('forbidden')
      else if (res.ok) {
        setStatus('ok')
        // Sync the composer to this conversation's stored model setting, once
        // per open/switch (the effect is keyed by conversationId). Skip it for a
        // conversation we just created locally — the composer already holds the
        // user's choice and the first send's model_setting write may not have
        // committed yet (a server read here would reset the picker to default).
        try {
          const convo = (await res.json()) as Conversation
          if (cancelled) return
          if (!consumeLocallyCreatedConversation(conversationId)) {
            const store = getPresetSelectionStore(wsId).getState()
            if (store.modelKey === before.modelKey && store.thinking === before.thinking) {
              store.setModelKey(convo.model_key ?? null)
              store.setThinking((convo.thinking ?? 'medium') as ThinkingLevel)
            }
          }
        } catch {
          // Best-effort: a malformed body just leaves the persisted default.
        } finally {
          if (!cancelled) setSyncedConvId(conversationId)
        }
      } else setStatus('notfound')
    })()
    loadArtifacts(client, conversationId)
    return () => {
      cancelled = true
    }
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
        <InputBar conversationId={conversationId} modelSyncPending={modelSyncPending} />
      </div>
    </AppShell>
  )
}
