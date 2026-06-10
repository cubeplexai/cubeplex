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
import { getPresetSelectionStore } from '@/lib/stores/preset-selection'
import { Box } from 'lucide-react'

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
            file_id: f.id,
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
      // Mirror InputBar.handleSubmit: the composer's preset + thinking choice
      // is a per-message field, so the home page's first-send path must read
      // and forward it too. Without this, the first message after opening a
      // new conversation always shipped as `thinking: "off"` regardless of
      // the dropdown — subsequent sends went through InputBar's own handler
      // and looked correct, which made the bug look like "the model picked
      // a different mode between turns."
      const selection = getPresetSelectionStore(wsId).getState()
      const sendOptions = {
        preset_label: selection.presetLabel,
        thinking: selection.thinking,
      }
      send(client, convId, content, attachedIds, optimisticAttachments, sendOptions).catch(
        (err) => {
          console.error('Failed to send message:', err)
        },
      )
      router.push(`/w/${wsId}/conversations/${convId}`)
    } catch (err) {
      console.error('Failed to create conversation:', err)
    }
  }

  return (
    <div className="flex-1 flex flex-col items-center justify-center">
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-primary/10 border border-primary/20 mb-5">
          <Box className="size-6 text-primary" strokeWidth={2} />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight mb-1.5">cubebox</h1>
        <p className="text-sm text-muted-foreground/70">{t('subtitle')}</p>
      </div>
      <div className="w-full max-w-2xl px-4">
        <InputBar
          conversationId={draftConvId ?? undefined}
          onCreateConversation={ensureConversation}
          onSubmit={handleSubmit}
        />
      </div>
    </div>
  )
}
