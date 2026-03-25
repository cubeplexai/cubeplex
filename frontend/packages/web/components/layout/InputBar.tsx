'use client'

import { useState } from 'react'
import { useMessageStore, createApiClient } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ArrowUp, Loader2 } from 'lucide-react'

interface InputBarProps {
  conversationId?: string
  onSubmit?: (content: string) => void
  isLoading?: boolean
}

export function InputBar({ conversationId, onSubmit, isLoading = false }: InputBarProps) {
  const [content, setContent] = useState('')
  const { sendMessage } = useMessageStore()
  const messageIsStreaming = useMessageStore((s) => s.isStreaming)

  const handleSubmit = async () => {
    if (!content.trim()) return
    if (!conversationId) {
      onSubmit?.(content)
      setContent('')
      return
    }

    const client = createApiClient('')
    try {
      setContent('')
      await sendMessage(client, conversationId, content)
    } catch (err) {
      console.error('Failed to send message:', err)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && e.ctrlKey) {
      handleSubmit()
    }
  }

  const isSubmitting = isLoading || messageIsStreaming

  return (
    <div className="w-full max-w-2xl mx-auto px-4">
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <Textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="有什么可以帮你的？"
          className="resize-none min-h-24"
          disabled={isSubmitting}
        />
        <div className="flex justify-end">
          <Button
            onClick={handleSubmit}
            disabled={!content.trim() || isSubmitting}
            size="sm"
          >
            {isSubmitting ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
          </Button>
        </div>
      </div>
    </div>
  )
}
