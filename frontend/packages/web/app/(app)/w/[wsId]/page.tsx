'use client'

import { use } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { createApiClient, useConversationStore, useMessageStore } from '@cubebox/core'
import { InputBar } from '@/components/layout/InputBar'
import { Box } from 'lucide-react'

export default function WorkspaceHomePage({ params }: { params: Promise<{ wsId: string }> }) {
  const t = useTranslations('home')
  const { wsId } = use(params)
  const router = useRouter()
  const { create: createConversation } = useConversationStore()
  const send = useMessageStore((s) => s.send)

  const handleSubmit = async (content: string) => {
    const client = createApiClient('')
    client.setWorkspaceId(wsId)
    try {
      const convo = await createConversation(client, content.slice(0, 30))
      useConversationStore.setState({ activeId: convo.id })
      router.push(`/w/${wsId}/conversations/${convo.id}`)
      send(client, convo.id, content).catch((err) => {
        console.error('Failed to send message:', err)
      })
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
        <InputBar onSubmit={handleSubmit} />
      </div>
    </div>
  )
}
