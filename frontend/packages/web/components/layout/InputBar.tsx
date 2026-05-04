'use client'

import { useState, useRef, useEffect } from 'react'
import { useTranslations } from 'next-intl'
import { useMessageStore, useAttachmentStore, createApiClient } from '@cubebox/core'
import { ArrowUp, Loader2, Paperclip, X } from 'lucide-react'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { AttachmentChips } from '@/components/chat/AttachmentChips'
import { UploadDropzone } from '@/components/chat/UploadDropzone'

interface InputBarProps {
  conversationId?: string
  onSubmit?: (content: string, files: File[]) => void | Promise<void>
  onCreateConversation?: () => Promise<string>
  isLoading?: boolean
}

function isInteractiveTarget(target: EventTarget): boolean {
  if (!(target instanceof Element)) return false
  return Boolean(target.closest('button,input,textarea,select,a,label,[role="button"]'))
}

export function InputBar({
  conversationId,
  onSubmit,
  onCreateConversation,
  isLoading = false,
}: InputBarProps): React.ReactElement {
  const t = useTranslations('input')
  const [content, setContent] = useState('')
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [isHandlingSubmit, setIsHandlingSubmit] = useState(false)
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
  const stagingItems = useAttachmentStore((s) =>
    conversationId ? (s.staging[conversationId] ?? []) : [],
  )
  const hydrate = useAttachmentStore((s) => s.hydrate)

  useEffect(() => {
    if (!conversationId) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    void hydrate(client, conversationId)
  }, [conversationId, workspaceId, hydrate])

  const uploadInFlight = stagingItems.some((u) => u.status === 'uploading')
  const isSubmitting = isLoading || messageIsStreaming || isHandlingSubmit
  const stagedFileCount = conversationId ? attachedIds.length : pendingFiles.length

  const resetTextareaHeight = (): void => {
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const handleSubmit = async (): Promise<void> => {
    if (isSubmitting || uploadInFlight || (!content.trim() && stagedFileCount === 0)) return
    if (!conversationId && !onSubmit) return

    try {
      setIsHandlingSubmit(true)
      if (onSubmit) {
        await onSubmit(content, [...pendingFiles])
        setContent('')
        setPendingFiles([])
        resetTextareaHeight()
        return
      }

      const client = createApiClient('')
      if (workspaceId) client.setWorkspaceId(workspaceId)
      const ids = [...attachedIds]
      const text = content
      setContent('')
      resetTextareaHeight()
      clearStaging(conversationId!)
      await send(client, conversationId!, text, ids)
    } catch (err) {
      console.error('Failed to send message:', err)
    } finally {
      setIsHandlingSubmit(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent): void => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handleSubmit()
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>): void => {
    setContent(e.target.value)
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 180) + 'px'
    }
  }

  const handleFiles = async (files: FileList | null): Promise<void> => {
    if (!files || !files.length) return
    const selectedFiles = Array.from(files)
    let convId = conversationId
    if (!convId && onCreateConversation) {
      try {
        convId = await onCreateConversation()
      } catch (err) {
        console.error('Failed to create conversation for upload:', err)
        return
      }
    }
    if (!convId) {
      if (onSubmit) setPendingFiles((current) => [...current, ...selectedFiles])
      return
    }
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    await upload(client, convId, selectedFiles)
  }

  const handleShellMouseDown = (e: React.MouseEvent<HTMLDivElement>): void => {
    if (isInteractiveTarget(e.target)) return
    e.preventDefault()
    textareaRef.current?.focus()
  }

  const removePendingFile = (index: number): void => {
    setPendingFiles((current) => current.filter((_, currentIndex) => currentIndex !== index))
  }

  const canAttach = Boolean(conversationId || onSubmit) && !isSubmitting

  return (
    <div className="w-full max-w-3xl mx-auto">
      {conversationId && <UploadDropzone conversationId={conversationId} />}
      {conversationId && <AttachmentChips conversationId={conversationId} />}
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
      {!conversationId && pendingFiles.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pb-2">
          {pendingFiles.map((file, index) => (
            <div
              key={`${file.name}-${file.lastModified}-${index}`}
              className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-2 py-1.5 text-xs"
            >
              <div className="flex flex-col leading-tight">
                <span className="max-w-[140px] truncate font-medium">{file.name}</span>
                <span className="text-[10px] text-muted-foreground">
                  {(file.size / 1024).toFixed(0)}KB
                </span>
              </div>
              <button
                type="button"
                onClick={() => removePendingFile(index)}
                className="ml-1 grid size-5 place-items-center rounded hover:bg-muted"
                aria-label={`Remove ${file.name}`}
              >
                <X className="size-3" />
              </button>
            </div>
          ))}
        </div>
      )}
      <div
        className="relative flex cursor-text items-end gap-2 rounded-xl border border-border bg-card px-3 py-2.5 transition-colors focus-within:border-primary/40"
        onMouseDown={handleShellMouseDown}
      >
        <button
          type="button"
          aria-label="Attach files"
          onClick={() => fileInputRef.current?.click()}
          disabled={!canAttach}
          className="grid size-7 shrink-0 cursor-pointer place-items-center rounded-lg text-muted-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-30"
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
          className="flex-1 bg-transparent resize-none outline-none text-sm text-foreground placeholder:text-muted-foreground/40 leading-relaxed min-h-7 max-h-[180px] overflow-y-auto py-0.5"
          disabled={isSubmitting}
        />
        <button
          data-testid="send-button"
          onClick={() => void handleSubmit()}
          disabled={(!content.trim() && stagedFileCount === 0) || isSubmitting || uploadInFlight}
          className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary text-white transition-all hover:bg-primary/80 disabled:cursor-not-allowed disabled:opacity-25"
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
