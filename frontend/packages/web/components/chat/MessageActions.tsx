'use client'

import { useId, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { GitBranch } from 'lucide-react'

import { createApiClient, useConversationStore, ApiError } from '@cubeplex/core'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

interface MessageActionsProps {
  conversationId: string
  workspaceId: string | null
  runId: string | null | undefined
  isGroupChat: boolean
  // Active-run guard: when this message belongs to the run that is still
  // streaming (or paused on HITL), the backend's ``cp.fork`` will reject
  // with ``run_not_completed`` because the run row's ``completion_seq``
  // is still NULL. Disable the button up-front instead of round-tripping
  // for a 400 toast.
  activeRunId: string | null
  isStreaming: boolean
}

/**
 * Hover-revealed action bar attached to each message bubble. Exposes
 * "Fork conversation" — copies the message history through the end of this
 * message's run into a fresh conversation. Disabled (with a tooltip
 * explanation) when the action can't be performed:
 *  - no run_id on this message (synthetic / pre-cubepi-v3 row)
 *  - the conversation is a group chat (server-side reject too)
 *
 * Parent positions this component (typically absolute, opacity-0 with
 * group-hover:opacity-100). MessageActions does not place itself; it just
 * renders the button + its disabled-state UI.
 */
export function MessageActions({
  conversationId,
  workspaceId,
  runId,
  isGroupChat,
  activeRunId,
  isStreaming,
}: MessageActionsProps) {
  const t = useTranslations('chat')
  const router = useRouter()
  const fork = useConversationStore((s) => s.fork)
  const [busy, setBusy] = useState(false)
  // Stable per-mount id for the disabled-state aria-describedby. Using
  // useId avoids collisions when several disabled fork buttons coexist on
  // a single page (every message without a run_id would otherwise share
  // one DOM id, breaking screen-reader association).
  const reactId = useId()
  const reasonId = `fork-disabled-${reactId}`

  const runStillRunning = runId != null && activeRunId === runId && isStreaming
  const disabledReason = !runId
    ? t('forkDisabled.noRun')
    : isGroupChat
      ? t('forkDisabled.groupChat')
      : runStillRunning
        ? t('forkDisabled.runStreaming')
        : null

  const handleFork = async () => {
    if (!runId || !workspaceId || busy) return
    setBusy(true)
    try {
      const client = createApiClient('')
      client.setWorkspaceId(workspaceId)
      const newConv = await fork(client, conversationId, runId)
      toast.success(t('forkSuccess'))
      router.push(`/w/${workspaceId}/conversations/${newConv.id}`)
    } catch (err) {
      const code = err instanceof ApiError ? err.code : null
      if (code === 'run_not_completed') {
        toast.error(t('forkDisabled.runStreaming'))
      } else if (code === 'group_chat_not_supported') {
        toast.error(t('forkDisabled.groupChat'))
      } else {
        toast.error(t('forkError'))
      }
      setBusy(false)
    }
  }

  const buttonClass = cn(
    'group/chip inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs',
    'text-muted-foreground hover:text-foreground hover:bg-muted/60',
    'disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent',
    'disabled:hover:text-muted-foreground transition-colors',
  )

  if (!disabledReason) {
    return (
      <button
        type="button"
        onClick={handleFork}
        disabled={busy}
        aria-label={t('forkConversation')}
        className={buttonClass}
      >
        <GitBranch className="size-3.5" />
        <span className="hidden group-hover/chip:inline">{t('forkConversation')}</span>
      </button>
    )
  }

  // Disabled state: use aria-disabled (NOT the native `disabled` attribute)
  // so the button stays in the tab order and the tooltip's hover/focus
  // detection still fires. The onClick is no-op'd while disabled; the
  // aria-describedby links the visible reason to the button for screen
  // readers, so the reason is announced even if the tooltip itself never
  // visually opens (e.g. mobile / no-pointer environments).
  return (
    <Tooltip>
      <TooltipTrigger
        type="button"
        aria-disabled="true"
        aria-label={t('forkConversation')}
        aria-describedby={reasonId}
        onClick={(e) => e.preventDefault()}
        className={cn(buttonClass, 'cursor-not-allowed opacity-60')}
      >
        <GitBranch className="size-3.5" />
        <span className="hidden group-hover/chip:inline">{t('forkConversation')}</span>
      </TooltipTrigger>
      <TooltipContent>
        <span id={reasonId}>{disabledReason}</span>
      </TooltipContent>
    </Tooltip>
  )
}
