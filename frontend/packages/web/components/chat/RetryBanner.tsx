'use client'

import { RotateCw } from 'lucide-react'
import type { RetryEvent } from '@/lib/types/events'
import { ASSISTANT_CONTENT_MAX_CLASS } from '@/lib/chatLayout'
import { cn } from '@/lib/utils'

interface RetryBannerProps {
  event: RetryEvent
}

function waitLabel(waitS: number): string {
  if (waitS <= 0) return ''
  const rounded = Number.isInteger(waitS) ? String(waitS) : waitS.toFixed(1)
  return `, waiting ${rounded}s`
}

/**
 * Inline banner shown while FallbackBoundModel retries the current model
 * before hopping. Replaced in place as later attempts arrive; hidden once
 * tokens start or a model_failover banner takes over.
 */
export function RetryBanner({ event }: RetryBannerProps) {
  const { model_ref, reason, attempt, wait_s } = event.data
  const summary = `Retrying ${model_ref} (attempt ${attempt}${waitLabel(wait_s)})`

  return (
    <div className={cn(ASSISTANT_CONTENT_MAX_CLASS)} data-testid="retry-banner">
      <details
        className="group rounded-md border border-border bg-card px-3 py-2
            text-xs text-muted-foreground"
      >
        <summary className="flex cursor-pointer list-none items-center gap-2 marker:hidden">
          <RotateCw className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="font-medium">{summary}</span>
        </summary>
        <p className="mt-1.5 ml-5 whitespace-pre-wrap break-words text-muted-foreground">
          {reason}
        </p>
      </details>
    </div>
  )
}
