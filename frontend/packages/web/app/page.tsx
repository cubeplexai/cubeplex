'use client'

import { useRouter } from 'next/navigation'
import { useConversationStore, useMessageStore, createApiClient } from '@cubebox/core'
import { InputBar } from '@/components/layout/InputBar'
import { Box } from 'lucide-react'

export default function WelcomePage() {
  const router = useRouter()
  const { create: createConversation } = useConversationStore()
  const { sendMessage } = useMessageStore()

  const handleSubmit = async (content: string) => {
    const client = createApiClient('')
    try {
      const convo = await createConversation(client, content.slice(0, 30))
      useConversationStore.setState({ activeId: convo.id })
      // 立即跳转，不等待消息发送完成
      router.push(`/conversations/${convo.id}`)
      // 在后台发送消息（sendMessage 会乐观地添加用户消息）
      sendMessage(client, convo.id, content)
    } catch (err) {
      console.error('Failed to create conversation:', err)
    }
  }

  return (
    <div className="h-screen flex flex-col items-center justify-center bg-background text-foreground">
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-primary/10 border border-primary/20 mb-5">
          <Box className="size-6 text-primary" strokeWidth={2} />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight mb-1.5">cubebox</h1>
        <p className="text-sm text-muted-foreground/70">AI 智能体系统</p>
      </div>
      <div className="w-full max-w-2xl px-4">
        <InputBar onSubmit={handleSubmit} />
      </div>
    </div>
  )
}
