'use client'

import { AlertTriangle } from 'lucide-react'
import type { FailoverEvent } from '@/lib/types/events'

interface FailoverBannerProps {
  event: FailoverEvent
}

/**
 * Inline gray banner rendered between chat messages whenever the backend
 * emits a `model_failover` SSE event. Two display shapes:
 *
 *   - `next_ref` non-null  → "Switched from <failed_ref> to <next_ref>"
 *   - `next_ref === null`  → "Failover exhausted on <failed_ref>"
 *
 * Never render the literal string "null" — `next_ref === null` means the
 * fallback chain ran out of legs (see backend `FallbackBoundModel`).
 *
 * Uses a native `<details>` element so the banner is keyboard-accessible
 * out of the box; expanding reveals the upstream `reason` string.
 */
export function FailoverBanner({ event }: FailoverBannerProps) {
  const { failed_ref, next_ref, reason } = event.data
  const exhausted = next_ref === null
  const summary = exhausted
    ? `Failover exhausted on ${failed_ref}`
    : `Switched from ${failed_ref} to ${next_ref}`

  return (
    <div className="flex justify-start gap-2.5" data-testid="failover-banner">
      <div className="shrink-0 w-6 h-6" />
      <div className="flex-1 max-w-[75%]">
        <details
          className="group rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2
            text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900/60 dark:text-zinc-400"
        >
          <summary className="flex cursor-pointer list-none items-center gap-2 marker:hidden">
            <AlertTriangle
              className="size-3.5 shrink-0 text-zinc-500 dark:text-zinc-500"
              aria-hidden
            />
            <span className="font-medium">{summary}</span>
          </summary>
          <p className="mt-1.5 ml-5 whitespace-pre-wrap break-words text-zinc-500 dark:text-zinc-500">
            {reason}
          </p>
        </details>
      </div>
    </div>
  )
}
