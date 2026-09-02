'use client'

import { useState, useRef, useEffect, useCallback, useMemo, useId } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { useShallow } from 'zustand/react/shallow'
import { toast } from 'sonner'
import useSWR from 'swr'
import {
  useMessageStore,
  useAttachmentStore,
  createApiClient,
  compactConversation,
  ApiError,
  type Message,
  type SkillSummary,
} from '@cubeplex/core'
import { ArrowUp, Loader2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { AttachmentChips } from '@/components/chat/AttachmentChips'
import { UploadDropzone } from '@/components/chat/UploadDropzone'
import { PendingSteers } from '@/components/layout/PendingSteers'
import { ModelPicker } from '@/components/chat/ModelPicker'
import { CommandPopover } from '@/components/chat/CommandPopover'
import { ComposerAddMenu } from '@/components/chat/ComposerAddMenu'
import { ComposerSkillsPicker } from '@/components/chat/ComposerSkillsPicker'
import { ComposerMcpPicker } from '@/components/chat/ComposerMcpPicker'
import { ComposerSkillChips } from '@/components/chat/ComposerSkillChips'
import { reasoningFromThinking } from '@/lib/reasoning-control'
import { getPresetSelectionStore, validatedModelKey } from '@/lib/stores/preset-selection'
import { useComposerDraft } from '@/hooks/useComposerDraft'
import { useComposerChromeStore } from '@/lib/stores/composer-chrome'
import { useMobileMenu } from '@/hooks/useMobileMenu'
import { applySkillChipsToContent, type ComposerSkillChip } from '@/lib/composer/skillChips'
import {
  filterSlashPalette,
  parseLeadingCommandToken,
  skillCommandsFromSummaries,
  SLASH_COMMANDS,
  type SlashCommand,
  type SlashCommandContext,
} from '@/lib/slash-commands'
import { CHAT_COLUMN_CLASS } from '@/lib/chatLayout'

async function fetchEnabledSkills(url: string): Promise<SkillSummary[]> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`skills fetch failed: ${res.status}`)
  return res.json() as Promise<SkillSummary[]>
}

/** In-composer overlays (mutually exclusive with each other). */
type ComposerPanel = 'slash' | 'plus' | 'skills' | 'mcp' | null

interface InputBarProps {
  conversationId?: string
  onSubmit?: (content: string, files: File[]) => void | Promise<void>
  onCreateConversation?: () => Promise<string>
  isLoading?: boolean
  // True while the opened conversation's stored model selection is still being
  // synced into the composer. New-turn sends are blocked until it resolves so a
  // send in the sync window can't ship the previous conversation's model. Steer
  // (mid-stream) is unaffected — it doesn't read the model selection.
  modelSyncPending?: boolean
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
  modelSyncPending = false,
}: InputBarProps): React.ReactElement {
  const t = useTranslations('input')
  const tShell = useTranslations('shellLayout')
  const tSlash = useTranslations('slashCommands')
  const router = useRouter()
  const [content, setContent] = useState('')
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [skillChips, setSkillChips] = useState<ComposerSkillChip[]>([])
  const [isHandlingSubmit, setIsHandlingSubmit] = useState(false)
  const [modelPickerOpen, setModelPickerOpen] = useState(false)
  const [composerPanel, setComposerPanel] = useState<ComposerPanel>(null)
  const [slashActiveIndex, setSlashActiveIndex] = useState(0)
  /** Esc dismisses until the draft changes (keeps `/…` text without reopening). */
  const [slashDismissed, setSlashDismissed] = useState(false)
  const send = useMessageStore((s) => s.send)
  const loadMessages = useMessageStore((s) => s.loadMessages)
  const cancelStream = useMessageStore((s) => s.cancelStream)
  const steer = useMessageStore((s) => s.steer)
  const appendHistoryMessage = useMessageStore((s) => s.appendHistoryMessage)
  const { workspaceId } = useWorkspaceContext()
  // Keep subscriptions unconditional: tests and non-workspace shells may have
  // no workspace id, but a workspace composer still needs fresh preset state.
  const presetStore = useMemo(
    () => getPresetSelectionStore(workspaceId ?? '__no-workspace__'),
    [workspaceId],
  )
  const presetFetchStatus = presetStore((s) => s.presetFetchStatus)
  const presets = presetStore((s) => s.presets)
  const noUsablePresets =
    Boolean(workspaceId) && presetFetchStatus === 'ready' && presets.length === 0
  const requestOpenShare = useComposerChromeStore((s) => s.requestOpenShare)
  const requestRename = useComposerChromeStore((s) => s.requestRename)
  const consumeRenameRequest = useComposerChromeStore((s) => s.consumeRenameRequest)
  const openMobileMenu = useMobileMenu((s) => s.open)
  const messageIsStreaming =
    useMessageStore((s) =>
      conversationId ? s.isStreaming && s.streamingConversationId === conversationId : false,
    ) ?? false
  // The pending slots are global to the store — the user only sees one
  // conversation at a time so we don't scope this hint per id. HITL itself
  // no longer locks the composer; text is routed through durable steering.
  const hasPendingHitl = useMessageStore(
    (s) => Object.keys(s.pendingConfirmMap).length > 0 || s.pendingAsk !== null,
  )
  const runLifecycle = useMessageStore((s) =>
    conversationId ? (s.runLifecycle[conversationId] ?? 'idle') : 'idle',
  )
  const isCancelling = useMessageStore((s) =>
    conversationId ? Boolean(s.cancellingConversationIds[conversationId]) : false,
  )
  const composerLockMessage = isCancelling ? t('cancellingLock') : null
  const shouldSteer =
    messageIsStreaming ||
    runLifecycle === 'running' ||
    runLifecycle === 'paused_hitl' ||
    runLifecycle === 'resuming_hitl'
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const slashListboxId = useId()

  const upload = useAttachmentStore((s) => s.upload)
  const clearStaging = useAttachmentStore((s) => s.clear)
  const attachedIds = useAttachmentStore(
    useShallow((s) => (conversationId ? s.attachedIds(conversationId) : [])),
  )
  const stagingItems = useAttachmentStore(
    useShallow((s) => (conversationId ? (s.staging[conversationId] ?? []) : [])),
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
  const pendingDraft = useComposerDraft((s) =>
    conversationId ? (s.pendingByConversation[conversationId] ?? s.pending) : s.pending,
  )
  const justConsumedRef = useRef(false)
  useEffect(() => {
    if (pendingDraft === null) return
    const targetConversationId = conversationId ?? null
    const consumed = useComposerDraft.getState().consume(targetConversationId)
    if (consumed === null) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- consume external draft on signal
    setContent((current) =>
      pendingDraft.placement === 'prepend' && current ? `${consumed}\n${current}` : consumed,
    )
    justConsumedRef.current = true
  }, [conversationId, pendingDraft])
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
  const hasSkillChips = skillChips.length > 0
  const stagedFileCount = conversationId ? attachedIds.length : pendingFiles.length
  const canSendPayload = hasText || stagedFileCount > 0 || hasSkillChips

  const resetTextareaHeight = (): void => {
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const closeComposerPanels = useCallback((): void => {
    setComposerPanel(null)
    setSlashDismissed(true)
  }, [])

  const openSkillsPicker = useCallback((): void => {
    setSlashDismissed(true)
    setModelPickerOpen(false)
    setComposerPanel('skills')
  }, [])

  const openMcpPicker = useCallback((): void => {
    setSlashDismissed(true)
    setModelPickerOpen(false)
    setComposerPanel('mcp')
  }, [])

  // Enabled workspace skills → dynamic `/skill-name` rows in the slash palette.
  const { data: enabledSkills } = useSWR<SkillSummary[]>(
    workspaceId ? `/api/v1/ws/${workspaceId}/skills` : null,
    fetchEnabledSkills,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  const handleSubmit = async (): Promise<void> => {
    const submittedText = applySkillChipsToContent(content, skillChips)
    if (
      isSubmitting ||
      shouldSteer ||
      uploadInFlight ||
      isCancelling ||
      modelSyncPending ||
      noUsablePresets ||
      !canSendPayload
    )
      return
    if (!conversationId && !onSubmit) return
    let clearedSubmittedText = false

    try {
      setIsHandlingSubmit(true)
      if (onSubmit) {
        await onSubmit(submittedText, [...pendingFiles])
        setContent('')
        setPendingFiles([])
        setSkillChips([])
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
      setContent('')
      setSkillChips([])
      clearedSubmittedText = true
      resetTextareaHeight()
      clearStaging(conversationId!)
      // Pull the per-workspace preset + thinking choice at send time so the
      // user's most recent toolbar change is always reflected (no stale
      // closure). Falls back to `undefined` when no workspace is available
      // (e.g. tests that render <InputBar onSubmit={...} /> without context),
      // which lets the backend use the workspace default.
      const selection = workspaceId ? getPresetSelectionStore(workspaceId).getState() : null
      const sendOptions = selection
        ? {
            model_key: validatedModelKey(selection),
            reasoning: reasoningFromThinking(selection.thinking),
          }
        : undefined
      await send(client, conversationId!, submittedText, ids, optimisticAttachments, sendOptions)
    } catch (err) {
      if (clearedSubmittedText) {
        setContent((current) =>
          submittedText ? (current ? `${submittedText}\n${current}` : submittedText) : current,
        )
      }
      if (err instanceof ApiError && err.status === 409 && err.code === 'active_run_conflict') {
        if (conversationId) {
          const recoveryClient = createApiClient('')
          if (workspaceId) recoveryClient.setWorkspaceId(workspaceId)
          await Promise.all([
            loadMessages(recoveryClient, conversationId, {
              force: true,
              preserveOtherConversationStream: true,
            }),
            hydrate(recoveryClient, conversationId),
          ])
        }
        toast.error(t('activeRunConflict'))
        return
      }
      console.error('Failed to send message:', err)
    } finally {
      setIsHandlingSubmit(false)
    }
  }

  const clearComposer = useCallback((): void => {
    setContent('')
    setSlashActiveIndex(0)
    setComposerPanel(null)
    resetTextareaHeight()
  }, [])

  const slashToken = parseLeadingCommandToken(content)
  const slashWantsOpen = !slashDismissed && slashToken !== null
  // Skills/MCP pickers take over the floating slot; hide slash while they are open.
  const slashOpen = slashWantsOpen && composerPanel !== 'skills' && composerPanel !== 'mcp'

  const runSlashCommand = useCallback(
    async (cmd: SlashCommand, ctx: SlashCommandContext): Promise<void> => {
      clearComposer()
      await cmd.run(ctx)
    },
    [clearComposer],
  )

  const slashCtx: SlashCommandContext = useMemo(
    () => ({
      conversationId,
      workspaceId: workspaceId ?? null,
      isStreaming: messageIsStreaming,
      effortAvailable: Boolean(workspaceId),
      modelPickerAvailable: Boolean(workspaceId),
      compactAvailable: true,
      cancelStream: (id: string) => {
        const client = createApiClient('')
        if (workspaceId) client.setWorkspaceId(workspaceId)
        void cancelStream(client, id)
      },
      openModelPicker: () => setModelPickerOpen(true),
      openEffortControl: () => setModelPickerOpen(true),
      startRename: () => {
        if (!conversationId) return
        // Ensure mobile drawer is open so the sidebar row can receive the request.
        openMobileMenu()
        requestRename(conversationId)
        // If no ConversationRow consumes the request, clear it and notify.
        window.setTimeout(() => {
          const pending = useComposerChromeStore.getState().renameRequest
          if (pending?.conversationId !== conversationId) return
          consumeRenameRequest(pending.nonce)
          toast.error(tSlash('renameUnavailable'))
        }, 400)
      },
      openAttach: () => fileInputRef.current?.click(),
      createNewChat: () => {
        if (workspaceId) {
          router.push(`/w/${workspaceId}`)
        }
      },
      openShare: () => {
        if (conversationId) requestOpenShare(conversationId)
      },
      openSkillsPicker: () => {
        if (workspaceId) openSkillsPicker()
      },
      openMcpPicker: () => {
        if (workspaceId) openMcpPicker()
      },
      pinSkill: (skill) => {
        setSkillChips((prev) => {
          if (prev.some((s) => s.id === skill.id)) return prev
          return [...prev, skill]
        })
      },
      compactConversation: async (id: string) => {
        const client = createApiClient('')
        if (workspaceId) client.setWorkspaceId(workspaceId)
        try {
          const result = await compactConversation(client, id)
          if (result.compacted) {
            // Durable marker is persisted server-side; append locally so the
            // timeline updates without a full bootstrap.
            if (result.marker) {
              appendHistoryMessage(id, result.marker as Message)
            }
            toast.success(tSlash('compactSuccess'))
          } else {
            toast.message(tSlash('compactSkipped'))
          }
        } catch (err) {
          console.error('Failed to compact conversation:', err)
          toast.error(tSlash('compactFailed'))
        }
      },
    }),
    [
      conversationId,
      workspaceId,
      messageIsStreaming,
      cancelStream,
      appendHistoryMessage,
      requestRename,
      consumeRenameRequest,
      openMobileMenu,
      requestOpenShare,
      openSkillsPicker,
      openMcpPicker,
      router,
      tSlash,
    ],
  )

  const slashQuery = slashToken?.query ?? ''
  const skillSlashCommands = useMemo(
    () => skillCommandsFromSummaries(enabledSkills ?? []),
    [enabledSkills],
  )
  const slashCommands = useMemo(
    () => filterSlashPalette(SLASH_COMMANDS, skillSlashCommands, slashQuery, slashCtx),
    [slashQuery, slashCtx, skillSlashCommands],
  )

  // Keep highlight in range when the filtered list shrinks.
  useEffect(() => {
    if (slashActiveIndex >= slashCommands.length) {
      setSlashActiveIndex(Math.max(0, slashCommands.length - 1))
    }
  }, [slashCommands.length, slashActiveIndex])

  const handleKeyDown = (e: React.KeyboardEvent): void => {
    if (e.nativeEvent.isComposing) return

    if (composerPanel === 'skills' || composerPanel === 'mcp') {
      if (e.key === 'Escape') {
        e.preventDefault()
        closeComposerPanels()
        return
      }
      // Let picker search inputs own navigation when focused; textarea Esc only.
    }

    if (slashOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        if (slashCommands.length === 0) return
        setSlashActiveIndex((i) => (i + 1) % slashCommands.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        if (slashCommands.length === 0) return
        setSlashActiveIndex((i) => (i - 1 + slashCommands.length) % slashCommands.length)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setSlashDismissed(true)
        return
      }
      if ((e.key === 'Enter' || e.key === 'Tab') && !e.shiftKey) {
        if (slashCommands.length > 0) {
          e.preventDefault()
          const cmd = slashCommands[slashActiveIndex] ?? slashCommands[0]
          if (cmd) void runSlashCommand(cmd, slashCtx)
          return
        }
        // Zero matches: fall through to normal Enter matrix.
        if (e.key === 'Tab') return
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (isCancelling) return
      if (shouldSteer && (hasText || hasSkillChips)) {
        void handleSteer()
      } else {
        void handleSubmit()
      }
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>): void => {
    const next = e.target.value
    setContent(next)
    setSlashDismissed(false)
    // Typing re-opens slash; close skills/mcp so the palette can appear.
    if (composerPanel === 'skills' || composerPanel === 'mcp' || composerPanel === 'plus') {
      setComposerPanel(null)
    }
    const ta = textareaRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 180) + 'px'
    }
  }

  /** Pin/unpin a skill chip, then close the picker so the user can type. */
  const selectSkillChip = useCallback(
    (skill: ComposerSkillChip): void => {
      setSkillChips((prev) => {
        if (prev.some((s) => s.id === skill.id)) {
          return prev.filter((s) => s.id !== skill.id)
        }
        return [...prev, skill]
      })
      closeComposerPanels()
      // Next frame so the panel unmounts before focus returns to the draft.
      requestAnimationFrame(() => textareaRef.current?.focus())
    },
    [closeComposerPanels],
  )

  const selectedSkillIds = useMemo(() => new Set(skillChips.map((s) => s.id)), [skillChips])

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
  const canAttach =
    Boolean(conversationId || onSubmit) && !isSubmitting && !shouldSteer && !isCancelling
  // Show Stop only while streaming AND the box is empty; once the user types
  // (or pins a skill chip), the button becomes Send (which steers the live run).
  const showStop =
    messageIsStreaming && Boolean(conversationId) && !hasText && !hasSkillChips && !isCancelling

  const handleCancel = async (): Promise<void> => {
    if (!conversationId) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    try {
      await cancelStream(client, conversationId)
    } catch (err) {
      console.error('Failed to cancel run:', err)
      toast.error(t('cancelFailed'))
    }
  }

  const handleSteer = async (): Promise<void> => {
    if (!conversationId || (!hasText && !hasSkillChips)) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    const text = applySkillChipsToContent(content, skillChips)
    setContent('')
    setSkillChips([])
    resetTextareaHeight()
    try {
      const accepted = await steer(client, conversationId, text)
      if (!accepted) {
        setContent((current) => (current ? `${text}\n${current}` : text))
      }
    } catch (err) {
      setContent((current) => (current ? `${text}\n${current}` : text))
      console.error('Failed to queue steering:', err)
      toast.error(t('steerFailed'))
    }
  }

  return (
    <div className={cn(CHAT_COLUMN_CLASS, 'pb-[env(safe-area-inset-bottom)]')}>
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
        className={cn(
          // Keep radius at lg (pre-change). Visible border + asymmetric inset
          // (more top, less bottom) sits the column lower in the frame.
          'relative flex flex-col rounded-lg border border-border bg-raised pt-3 pb-1.5 shadow-sm transition duration-base',
          'focus-within:border-border-strong focus-within:ring-2 focus-within:ring-ring/20',
          'has-[[aria-expanded=true]]:border-border-strong has-[[aria-expanded=true]]:ring-2 has-[[aria-expanded=true]]:ring-ring/20',
        )}
        onMouseDown={handleShellMouseDown}
      >
        <CommandPopover
          open={slashOpen}
          commands={slashCommands}
          activeIndex={slashActiveIndex}
          onActiveIndexChange={setSlashActiveIndex}
          onSelect={(cmd) => void runSlashCommand(cmd, slashCtx)}
          listboxId={slashListboxId}
        />
        {workspaceId && (
          <>
            <ComposerSkillsPicker
              open={composerPanel === 'skills'}
              workspaceId={workspaceId}
              selectedIds={selectedSkillIds}
              onToggle={selectSkillChip}
              onClose={closeComposerPanels}
            />
            <ComposerMcpPicker
              open={composerPanel === 'mcp'}
              workspaceId={workspaceId}
              onClose={closeComposerPanels}
            />
          </>
        )}
        <ComposerSkillChips
          skills={skillChips}
          onRemove={(id) => setSkillChips((prev) => prev.filter((s) => s.id !== id))}
          className="px-2.5 pb-1"
        />
        <textarea
          ref={textareaRef}
          data-testid="chat-input"
          value={content}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={
            composerLockMessage ?? (hasPendingHitl ? t('pendingHitlSteerHint') : t('placeholder'))
          }
          title={composerLockMessage ?? undefined}
          rows={1}
          role="combobox"
          aria-expanded={slashOpen}
          aria-controls={slashOpen ? slashListboxId : undefined}
          aria-autocomplete="list"
          className="resize-none bg-transparent outline-none text-md text-foreground placeholder:text-muted-foreground/60 leading-relaxed min-h-7 max-h-[180px] overflow-y-auto px-3.5 py-1 disabled:cursor-not-allowed"
          disabled={(isSubmitting && !shouldSteer) || composerLockMessage !== null}
        />
        <div className="flex items-end gap-1 px-2 pt-1 pb-0.5">
          <ComposerAddMenu
            open={composerPanel === 'plus'}
            onOpenChange={(open) => {
              if (open) {
                setSlashDismissed(true)
                setModelPickerOpen(false)
                setComposerPanel('plus')
                return
              }
              // Functional update so choosing Skills/MCP (which sets another
              // panel in the same click) is not clobbered by menu close.
              setComposerPanel((prev) => (prev === 'plus' ? null : prev))
            }}
            disabled={!canAttach && !workspaceId}
            canAttach={canAttach}
            canSkills={Boolean(workspaceId)}
            canMcp={Boolean(workspaceId)}
            onAttach={() => fileInputRef.current?.click()}
            onSkills={openSkillsPicker}
            onMcp={openMcpPicker}
          />
          <div className="ml-auto flex items-end gap-1">
            {workspaceId && (
              <ModelPicker
                wsId={workspaceId}
                open={modelPickerOpen}
                onOpenChange={setModelPickerOpen}
              />
            )}
            {showStop ? (
              <button
                data-testid="stop-button"
                type="button"
                onClick={() => void handleCancel()}
                aria-label={tShell('inputBarStop')}
                className="group relative flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground transition-all duration-fast hover:bg-primary/80 active:scale-[0.94]"
              >
                <Loader2 className="absolute inset-0 m-auto size-5 animate-spin opacity-90" />
                <span className="relative size-2 rounded-xs bg-primary-foreground transition-transform group-hover:scale-110" />
              </button>
            ) : (
              <button
                data-testid="send-button"
                type="button"
                onClick={() => void (shouldSteer ? handleSteer() : handleSubmit())}
                disabled={
                  !canSendPayload ||
                  (isSubmitting && !shouldSteer) ||
                  uploadInFlight ||
                  isCancelling ||
                  (!shouldSteer && (modelSyncPending || noUsablePresets))
                }
                title={composerLockMessage ?? undefined}
                className={cn(
                  'flex size-7 shrink-0 items-center justify-center rounded-md transition-all duration-fast active:scale-[0.94]',
                  canSendPayload && !isCancelling
                    ? 'bg-primary text-primary-foreground hover:bg-primary/80'
                    : 'bg-muted text-muted-foreground',
                  'disabled:cursor-not-allowed disabled:opacity-40 disabled:active:scale-100',
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
