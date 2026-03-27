'use client'

import { useState, useRef } from 'react'
import { useMessageStore, createApiClient } from '@cubebox/core'
import { ArrowUp, Loader2 } from 'lucide-react'

interface InputBarProps {
  conversationId?: string
  onSubmit?: (content: string) => void
  isLoading?: boolean
}

export function InputBar({ conversationId, onSubmit, isLoading = false }: InputBarProps) {
  const [content, setContent] = useState('')
  const { sendMessage } = useMessageStore()
  const messageIsStreaming = useMessageStore((s) =>
    conversationId ? s.streamingConversationId === conversationId : false
  )
  const textareaRef = useRef<HTMLTextAreaElement>(null)

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
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto'
      }
      await sendMessage(client, conversationId, content)
    } catch (err) {
      console.error('Failed to send message:', err)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Skip if IME is composing (e.g., selecting Chinese characters)
    if (e.nativeEvent.isComposing) return

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setContent(e.target.value)
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 180) + 'px'
    }
  }

  const isSubmitting = isLoading || messageIsStreaming

  return (
    <div className="w-full max-w-3xl mx-auto">
      <div className="relative flex items-end bg-card border border-border rounded-xl px-3 py-2.5 gap-2 focus-within:border-primary/40 transition-colors">
        <textarea
          ref={textareaRef}
          value={content}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="有什么可以帮你的？"
          rows={1}
          className="flex-1 bg-transparent resize-none outline-none text-sm text-foreground placeholder:text-muted-foreground/40 leading-relaxed min-h-[22px] max-h-[180px] overflow-y-auto py-0.5"
          disabled={isSubmitting}
        />
        <button
          onClick={handleSubmit}
          disabled={!content.trim() || isSubmitting}
          className="shrink-0 w-7 h-7 flex items-center justify-center rounded-lg bg-primary text-white hover:bg-primary/80 disabled:opacity-25 disabled:cursor-not-allowed transition-all"
        >
          {isSubmitting
            ? <Loader2 className="size-3.5 animate-spin" />
            : <ArrowUp className="size-3.5" />
          }
        </button>
      </div>
      <p className="text-center mt-1 text-[10px] text-muted-foreground/35">Enter 发送 / Shift+Enter 换行</p>
    </div>
  )
}
