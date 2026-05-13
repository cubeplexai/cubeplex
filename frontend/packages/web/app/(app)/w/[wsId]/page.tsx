'use client'

import { use, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import {
  createApiClient,
  useAttachmentStore,
  useConversationStore,
  useMessageStore,
} from '@cubebox/core'
import { InputBar } from '@/components/layout/InputBar'

export default function WorkspaceHomePage({
  params,
}: {
  params: Promise<{ wsId: string }>
}): React.ReactElement {
  const t = useTranslations('home')
  const { wsId } = use(params)
  const router = useRouter()
  const { create: createConversation, rename: renameConversation } = useConversationStore()
  const send = useMessageStore((s) => s.send)
  const [draftConvId, setDraftConvId] = useState<string | null>(null)

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (draftConvId) return draftConvId
    const client = createApiClient('')
    client.setWorkspaceId(wsId)
    const convo = await createConversation(client, '', { draft: true })
    useConversationStore.setState({ activeId: convo.id })
    setDraftConvId(convo.id)
    return convo.id
  }, [draftConvId, wsId, createConversation])

  const handleSubmit = async (content: string): Promise<void> => {
    const client = createApiClient('')
    client.setWorkspaceId(wsId)
    try {
      const convId = await ensureConversation()

      const stagingItems = useAttachmentStore.getState().staging[convId] ?? []
      const attachedIds = useAttachmentStore.getState().attachedIds(convId)
      if (!content.trim() && attachedIds.length === 0) return

      // Snapshot attachment metadata so the optimistic user message renders
      // attachments above the bubble during streaming, matching the
      // post-refresh layout where MessageList reads them from history.
      const optimisticAttachments = stagingItems
        .filter((u) => u.status === 'done' && u.serverFile)
        .map((u) => {
          const f = u.serverFile!
          return {
            id: f.id,
            filename: f.filename,
            kind: f.kind,
            size_bytes: f.size_bytes,
            width: f.width,
            height: f.height,
            thumbnail_url: f.thumbnail_url,
            download_url: f.download_url,
          }
        })

      // Only stamp a placeholder title for files-only submissions. When the
      // user typed text, leave title empty so the backend's generate-title
      // service can produce an LLM title — preempting it here would trip
      // the "already-titled" gate in conversation_title.py and silently
      // skip auto-generation.
      if (!content.trim() && attachedIds.length > 0) {
        await renameConversation(client, convId, 'Files').catch((err) => {
          console.error('Failed to set conversation title:', err)
        })
      }

      useAttachmentStore.getState().clear(convId)
      send(client, convId, content, attachedIds, optimisticAttachments).catch((err) => {
        console.error('Failed to send message:', err)
      })
      router.push(`/w/${wsId}/conversations/${convId}`)
    } catch (err) {
      console.error('Failed to create conversation:', err)
    }
  }

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6">
      <div className="w-full max-w-2xl">
        <div className="mb-7">
          <p className="op-eyebrow mb-2">{t('eyebrow')}</p>
          <h1 className="text-[26px] font-semibold tracking-tight leading-tight text-foreground">
            {t('title')}
          </h1>
          <p className="mt-2 text-[13.5px] text-muted-foreground max-w-prose leading-relaxed">
            {t('lede')}
          </p>
        </div>
        <InputBar
          conversationId={draftConvId ?? undefined}
          onCreateConversation={ensureConversation}
          onSubmit={handleSubmit}
        />
        <div className="mt-3 flex items-center gap-3 text-[11.5px] text-muted-foreground font-mono">
          <span>
            <span className="op-kbd">⏎</span> {t('kbdSend')}
          </span>
          <span className="text-border">·</span>
          <span>
            <span className="op-kbd">⇧</span>
            <span className="op-kbd ml-0.5">⏎</span> {t('kbdNewline')}
          </span>
          <span className="text-border">·</span>
          <span>
            <span className="op-kbd">⌘</span>
            <span className="op-kbd ml-0.5">K</span> {t('kbdPalette')}
          </span>
        </div>
      </div>
    </div>
  )
}
