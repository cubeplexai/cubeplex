'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { AlertCircle, Check, Copy, Info, Loader2 } from 'lucide-react'
import type { ErrorEventData } from '@cubeplex/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { cn } from '@/lib/utils'
import type { RunChipStatus } from '@/lib/runChipStatus'
import { RunErrorBubble } from './RunErrorBubble'

function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text)
  }
  return new Promise((resolve, reject) => {
    try {
      const el = document.createElement('textarea')
      el.value = text
      el.style.cssText = 'position:fixed;opacity:0;top:0;left:0'
      document.body.appendChild(el)
      el.focus()
      el.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(el)
      if (ok) {
        resolve()
      } else {
        reject(new Error('execCommand failed'))
      }
    } catch (e) {
      reject(e)
    }
  })
}

const STATUS_LABEL: Record<Exclude<RunChipStatus, 'completed'>, string> = {
  stopping: 'statusStopping',
  stopped: 'statusStopped',
  reconnecting: 'statusReconnecting',
  disconnected: 'statusDisconnected',
  failed: 'statusFailed',
  incomplete: 'statusIncomplete',
}

const STATUS_ARIA: Record<Exclude<RunChipStatus, 'completed'>, string> = {
  stopping: 'ariaStopping',
  stopped: 'ariaStopped',
  reconnecting: 'ariaReconnecting',
  disconnected: 'ariaDisconnected',
  failed: 'ariaFailed',
  incomplete: 'ariaIncomplete',
}

const STATUS_DETAIL: Record<Exclude<RunChipStatus, 'completed'>, string> = {
  stopping: 'detailStopping',
  stopped: 'detailStopped',
  reconnecting: 'detailReconnecting',
  disconnected: 'detailDisconnected',
  failed: 'detailFailed',
  incomplete: 'detailIncomplete',
}

interface RunInfoChipProps {
  runId: string | null | undefined
  status?: RunChipStatus
  error?: ErrorEventData | null
  onRetry?: () => void
}

/**
 * Run-level status + debug id. Completed turns stay a hover Info chip.
 * Stopped / reconnect / failed / incomplete stay visible with a short label.
 */
export function RunInfoChip({
  runId,
  status = 'completed',
  error = null,
  onRetry,
}: RunInfoChipProps) {
  const t = useTranslations('chat.runInfo')
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  if (!runId && status === 'completed') return null

  const handleCopy = async () => {
    if (!runId) return
    try {
      await copyText(runId)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard failures are rare; leave the id visible so the user can select it.
    }
  }

  const isLive = status === 'reconnecting' || status === 'stopping'
  const showRetry = status === 'disconnected' && onRetry != null
  const triggerClass = cn(
    'group/chip inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors',
    status === 'completed' && 'text-muted-foreground hover:text-foreground hover:bg-muted/60',
    status === 'stopped' && 'text-muted-foreground hover:bg-muted/60',
    (status === 'reconnecting' || status === 'disconnected' || status === 'incomplete') &&
      'text-warning-fg hover:bg-warning-surface',
    status === 'stopping' && 'text-muted-foreground hover:bg-muted/60',
    status === 'failed' && 'text-danger-fg hover:bg-danger-surface',
  )

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-label={status === 'completed' ? t('ariaLabel') : t(STATUS_ARIA[status])}
            className={triggerClass}
          >
            {isLive ? (
              <Loader2 aria-hidden className="size-3.5 animate-spin" />
            ) : status === 'failed' || status === 'incomplete' ? (
              <AlertCircle aria-hidden className="size-3.5" />
            ) : (
              <Info aria-hidden className="size-3.5" />
            )}
            {status === 'completed' ? (
              <span className="hidden group-hover/chip:inline">{t('label')}</span>
            ) : (
              <span>{t(STATUS_LABEL[status])}</span>
            )}
          </button>
        }
      />
      <PopoverContent
        align="start"
        sideOffset={8}
        className="w-auto max-w-sm gap-0 space-y-2 border border-border px-3 py-2.5 text-xs
          text-muted-foreground"
      >
        {status !== 'completed' && (
          <p className="text-foreground/80 leading-snug">{t(STATUS_DETAIL[status])}</p>
        )}
        {status === 'failed' && error && error.error_code && <RunErrorBubble data={error} />}
        {status === 'failed' && error?.message && (
          <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-snug max-h-40 overflow-auto">
            {error.message}
          </pre>
        )}
        {runId && (
          <div className="space-y-1.5">
            <div className="font-medium text-foreground/70">{t('runIdLabel')}</div>
            <div className="flex items-center gap-1.5">
              <code
                className="min-w-0 flex-1 break-all font-mono text-[11px] leading-snug
                  text-foreground/90 select-all"
              >
                {runId}
              </code>
              <button
                type="button"
                onClick={handleCopy}
                aria-label={copied ? t('copied') : t('copy')}
                className="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1
                  text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
              >
                {copied ? (
                  <Check className="size-3.5 text-success" />
                ) : (
                  <Copy className="size-3.5" />
                )}
              </button>
            </div>
          </div>
        )}
        {showRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="rounded-md px-2 py-1 text-xs text-foreground hover:bg-muted/60"
          >
            {t('retry')}
          </button>
        )}
      </PopoverContent>
    </Popover>
  )
}
