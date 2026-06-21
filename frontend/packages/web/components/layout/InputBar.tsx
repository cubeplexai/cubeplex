'use client'

import { useState, useRef, useEffect } from 'react'
import { useTranslations } from 'next-intl'
import { useMessageStore, useAttachmentStore, createApiClient } from '@cubebox/core'
import { ArrowUp, Loader2, Paperclip, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { AttachmentChips } from '@/components/chat/AttachmentChips'
import { UploadDropzone } from '@/components/chat/UploadDropzone'
import { PendingSteers } from '@/components/layout/PendingSteers'
import { ModelPicker } from '@/components/chat/ModelPicker'
import { getPresetSelectionStore, validatedModelKey } from '@/lib/stores/preset-selection'
import { useComposerDraft } from '@/hooks/useComposerDraft'

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
  const tShell = useTranslations('shellLayout')
  const [content, setContent] = useState('')
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [isHandlingSubmit, setIsHandlingSubmit] = useState(false)
  const send = useMessageStore((s) => s.send)
  const cancelStream = useMessageStore((s) => s.cancelStream)
  const steer = useMessageStore((s) => s.steer)
  const { workspaceId } = useWorkspaceContext()
  const messageIsStreaming =
    useMessageStore((s) =>
      conversationId ? s.isStreaming && s.streamingConversationId === conversationId : false,
    ) ?? false
  // Composer lock: while a HITL request is pending (live SSE or bootstrap
  // cold-start seed), block both fresh-turn send and mid-stream steer until
  // the user answers the card. The pending slots are global to the store —
  // the user only sees one conversation at a time so we don't scope per-id.
  const hasPendingHitl = useMessageStore(
    (s) => Object.keys(s.pendingConfirmMap).length > 0 || s.pendingAsk !== null,
  )
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

  // Composer-draft bridge: PromptCards (or other callers) push a string
  // into useComposerDraft; we consume it once into local content. We
  // subscribe to the nonce so re-clicking the same card re-injects the
  // text even when its value is unchanged.
  // We track "just consumed" with a ref so the height-sync runs on the
  // NEXT render — when React has actually committed the new content and
  // the textarea's scrollHeight reflects it. Doing the resize in the
  // same effect would read scrollHeight from the pre-setContent textarea.
  const pendingDraft = useComposerDraft((s) => s.pending)
  const justConsumedRef = useRef(false)
  useEffect(() => {
    if (pendingDraft === null) return
    const consumed = useComposerDraft.getState().consume()
    if (consumed === null) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- consume external draft on signal
    setContent(consumed)
    justConsumedRef.current = true
  }, [pendingDraft])
  // Height sync runs AFTER content commits; the [content] dep guarantees
  // scrollHeight is measured from the latest textarea value.
  useEffect(() => {
    if (!justConsumedRef.current) return
    justConsumedRef.current = false
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 180) + 'px'
    ta.focus()
  }, [content])

  const uploadInFlight = stagingItems.some((u) => u.status === 'uploading')
  // Streaming no longer locks the textarea — the user can type to steer.
  // handleSubmit still guards against starting a *new* turn mid-stream via
  // `messageIsStreaming` directly (see handleSubmit).
  const isSubmitting = isLoading || isHandlingSubmit
  const hasText = content.trim().length > 0
  const stagedFileCount = conversationId ? attachedIds.length : pendingFiles.length

  const resetTextareaHeight = (): void => {
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const handleSubmit = async (): Promise<void> => {
    if (
      isSubmitting ||
      messageIsStreaming ||
      uploadInFlight ||
      hasPendingHitl ||
      (!content.trim() && stagedFileCount === 0)
    )
      return
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
      const text = content
      setContent('')
      resetTextareaHeight()
      clearStaging(conversationId!)
      // Pull the per-workspace preset + thinking choice at send time so the
      // user's most recent toolbar change is always reflected (no stale
      // closure). Falls back to `undefined` when no workspace is available
      // (e.g. tests that render <InputBar onSubmit={...} /> without context),
      // which lets the backend use the workspace default.
      const selection = workspaceId ? getPresetSelectionStore(workspaceId).getState() : null
      const sendOptions = selection
        ? { model_key: validatedModelKey(selection), thinking: selection.thinking }
        : undefined
      await send(client, conversationId!, text, ids, optimisticAttachments, sendOptions)
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
      if (hasPendingHitl) return
      if (messageIsStreaming && hasText) {
        void handleSteer()
      } else {
        void handleSubmit()
      }
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

  // Steering is text-only; don't allow new attachment uploads mid-run.
  const canAttach = Boolean(conversationId || onSubmit) && !isSubmitting && !messageIsStreaming
  // Show Stop only while streaming AND the box is empty; once the user types,
  // the button becomes Send (which steers the live run).
  const showStop = messageIsStreaming && Boolean(conversationId) && !hasText

  const handleCancel = async (): Promise<void> => {
    if (!conversationId) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    await cancelStream(client, conversationId)
  }

  const handleSteer = async (): Promise<void> => {
    if (!conversationId || !hasText) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    const text = content
    setContent('')
    resetTextareaHeight()
    await steer(client, conversationId, text)
  }

  return (
    <div className="w-full max-w-3xl mx-auto pb-[env(safe-area-inset-bottom)]">
      {conversationId && <PendingSteers conversationId={conversationId} />}
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
        className="flex flex-col rounded-lg border border-transparent bg-raised transition focus-within:border-primary focus-within:ring-2 focus-within:ring-ring/30 has-[[aria-expanded=true]]:border-primary has-[[aria-expanded=true]]:ring-2 has-[[aria-expanded=true]]:ring-ring/30 duration-base"
        onMouseDown={handleShellMouseDown}
      >
        <textarea
          ref={textareaRef}
          data-testid="chat-input"
          value={content}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={hasPendingHitl ? t('pendingHitlLock') : t('placeholder')}
          title={hasPendingHitl ? t('pendingHitlLock') : undefined}
          rows={1}
          className="resize-none bg-transparent outline-none text-md text-foreground placeholder:text-muted-foreground/60 leading-relaxed min-h-7 max-h-[180px] overflow-y-auto px-3.5 pt-3 pb-1 disabled:cursor-not-allowed"
          disabled={(isSubmitting && !messageIsStreaming) || hasPendingHitl}
        />
        <div className="flex items-center gap-1 px-2 pb-2">
          <button
            type="button"
            aria-label={tShell('inputBarAttach')}
            onClick={() => fileInputRef.current?.click()}
            disabled={!canAttach}
            className="grid size-7 shrink-0 cursor-pointer place-items-center rounded text-muted-foreground hover:bg-accent transition-colors duration-fast disabled:cursor-not-allowed disabled:opacity-30"
          >
            <Paperclip className="size-3.5" />
          </button>
          <div className="ml-auto flex items-center gap-1">
            {workspaceId && (
              <>
                <ModelPicker wsId={workspaceId} />
              </>
            )}
            {showStop ? (
              <button
                data-testid="stop-button"
                type="button"
                onClick={() => void handleCancel()}
                aria-label={tShell('inputBarStop')}
                className="group relative flex size-7 shrink-0 items-center justify-center rounded bg-primary text-primary-foreground transition-all duration-fast hover:bg-primary/80"
              >
                <Loader2 className="absolute inset-0 m-auto size-5 animate-spin opacity-90" />
                <span className="relative size-2 rounded-xs bg-primary-foreground transition-transform group-hover:scale-110" />
              </button>
            ) : (
              <button
                data-testid="send-button"
                onClick={() => void (messageIsStreaming ? handleSteer() : handleSubmit())}
                disabled={
                  (!content.trim() && stagedFileCount === 0) ||
                  (isSubmitting && !messageIsStreaming) ||
                  uploadInFlight ||
                  hasPendingHitl
                }
                title={hasPendingHitl ? t('pendingHitlLock') : undefined}
                className={cn(
                  'flex size-7 shrink-0 items-center justify-center rounded bg-primary text-primary-foreground transition-all duration-fast hover:bg-primary/80',
                  'disabled:cursor-not-allowed disabled:opacity-25',
                )}
              >
                {isSubmitting ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <ArrowUp className="size-3.5" />
                )}
              </button>
            )}
          </div>
        </div>
      </div>
      <p className="text-center mt-1 text-2xs text-faint">{t('hint')}</p>
    </div>
  )
}
