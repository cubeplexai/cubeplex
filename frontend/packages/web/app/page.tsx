'use client'

import { useRouter } from 'next/navigation'
import { useConversationStore, createApiClient } from '@cubebox/core'
import { InputBar } from '@/components/layout/InputBar'

export default function WelcomePage() {
  const router = useRouter()
  const { create: createConversation } = useConversationStore()

  const handleSubmit = async (content: string) => {
    const client = createApiClient('')
    try {
      const convo = await createConversation(client, content.slice(0, 30))
      useConversationStore.setState({ activeId: convo.id })
      router.push(`/conversations/${convo.id}`)
    } catch (err) {
      console.error('Failed to create conversation:', err)
    }
  }

  return (
    <div className="h-screen flex flex-col items-center justify-center bg-background text-foreground">
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold mb-2">cubebox</h1>
        <p className="text-muted-foreground">AI 智能体系统</p>
      </div>
      <InputBar onSubmit={handleSubmit} />
    </div>
  )
}
