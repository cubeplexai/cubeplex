'use client'

import { useState, useRef, useEffect } from 'react'
import { useTranslations } from 'next-intl'
import { useMessageStore, useAttachmentStore, createApiClient } from '@cubebox/core'
import { ArrowUp, Loader2, Paperclip } from 'lucide-react'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { AttachmentChips } from '@/components/chat/AttachmentChips'
import { UploadDropzone } from '@/components/chat/UploadDropzone'

interface InputBarProps {
  conversationId?: string
  onSubmit?: (content: string) => void
  isLoading?: boolean
}

export function InputBar({ conversationId, onSubmit, isLoading = false }: InputBarProps) {
  const t = useTranslations('input')
  const [content, setContent] = useState('')
  const send = useMessageStore((s) => s.send)
  const { workspaceId } = useWorkspaceContext()
  const messageIsStreaming =
    useMessageStore((s) =>
      conversationId ? s.isStreaming && s.streamingConversationId === conversationId : false,
    ) ?? false
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const upload = useAttachmentStore((s) => s.upload)
  const clearStaging = useAttachmentStore((s) => s.clear)
  const attachedIds = useAttachmentStore((s) =>
    conversationId ? s.attachedIds(conversationId) : [],
  )
  const hydrate = useAttachmentStore((s) => s.hydrate)

  useEffect(() => {
    if (!conversationId) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    void hydrate(client, conversationId)
  }, [conversationId, workspaceId, hydrate])

  const handleSubmit = async () => {
    if (!content.trim() && attachedIds.length === 0) return
    if (!conversationId) {
      onSubmit?.(content)
      setContent('')
      return
    }
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    try {
      const ids = [...attachedIds]
      const text = content
      setContent('')
      if (textareaRef.current) textareaRef.current.style.height = 'auto'
      clearStaging(conversationId)
      await send(client, conversationId, text, ids)
    } catch (err) {
      console.error('Failed to send message:', err)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
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

  const handleFiles = async (files: FileList | null) => {
    if (!files || !files.length || !conversationId) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    await upload(client, conversationId, Array.from(files))
  }

  const isSubmitting = isLoading || messageIsStreaming

  return (
    <div className="w-full max-w-3xl mx-auto">
      {conversationId && <UploadDropzone conversationId={conversationId} />}
      {conversationId && <AttachmentChips conversationId={conversationId} />}
      <div className="relative flex items-end bg-card border border-border rounded-xl px-3 py-2.5 gap-2 focus-within:border-primary/40 transition-colors">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            void handleFiles(e.target.files)
            e.target.value = ''
          }}
        />
        <button
          type="button"
          aria-label="Attach files"
          onClick={() => fileInputRef.current?.click()}
          disabled={!conversationId || isSubmitting}
          className="shrink-0 grid place-items-center w-7 h-7 rounded-lg text-muted-foreground hover:bg-muted disabled:opacity-30"
        >
          <Paperclip className="size-3.5" />
        </button>
        <textarea
          ref={textareaRef}
          data-testid="chat-input"
          value={content}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={t('placeholder')}
          rows={1}
          className="flex-1 bg-transparent resize-none outline-none text-sm text-foreground placeholder:text-muted-foreground/40 leading-relaxed min-h-[22px] max-h-[180px] overflow-y-auto py-0.5"
          disabled={isSubmitting}
        />
        <button
          data-testid="send-button"
          onClick={handleSubmit}
          disabled={(!content.trim() && attachedIds.length === 0) || isSubmitting}
          className="shrink-0 w-7 h-7 flex items-center justify-center rounded-lg bg-primary text-white hover:bg-primary/80 disabled:opacity-25 disabled:cursor-not-allowed transition-all"
        >
          {isSubmitting ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <ArrowUp className="size-3.5" />
          )}
        </button>
      </div>
      <p className="text-center mt-1 text-[10px] text-muted-foreground/35">{t('hint')}</p>
    </div>
  )
}
