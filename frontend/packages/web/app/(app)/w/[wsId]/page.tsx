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
import { Box } from 'lucide-react'

export default function WorkspaceHomePage({
  params,
}: {
  params: Promise<{ wsId: string }>
}): React.ReactElement {
  const t = useTranslations('home')
  const { wsId } = use(params)
  const router = useRouter()
  const { create: createConversation } = useConversationStore()
  const send = useMessageStore((s) => s.send)
  const [draftConvId, setDraftConvId] = useState<string | null>(null)

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (draftConvId) return draftConvId
    const client = createApiClient('')
    client.setWorkspaceId(wsId)
    const convo = await createConversation(client, 'New chat')
    useConversationStore.setState({ activeId: convo.id })
    setDraftConvId(convo.id)
    return convo.id
  }, [draftConvId, wsId, createConversation])

  const handleSubmit = async (content: string): Promise<void> => {
    const client = createApiClient('')
    client.setWorkspaceId(wsId)
    try {
      const convId = await ensureConversation()

      const attachedIds = useAttachmentStore.getState().attachedIds(convId)
      if (!content.trim() && attachedIds.length === 0) return

      const title = content.trim() ? content.trim().slice(0, 30) : 'Files'
      await client.put(`/api/v1/conversations/${convId}/title`, { title }).catch(() => {})

      useAttachmentStore.getState().clear(convId)
      send(client, convId, content, attachedIds).catch((err) => {
        console.error('Failed to send message:', err)
      })
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
