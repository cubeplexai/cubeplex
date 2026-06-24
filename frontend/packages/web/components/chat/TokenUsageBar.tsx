'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { BarChart3 } from 'lucide-react'
import type { TurnUsage, SessionUsage } from '@cubebox/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { computeCacheHitRate, formatPercent } from '@/lib/cost/helpers'

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function progressColor(pct: number): string {
  if (pct >= 80) return 'bg-danger-solid'
  if (pct >= 50) return 'bg-warning-solid'
  return 'bg-success-solid'
}

interface TokenUsageBarProps {
  turnUsage: TurnUsage | null
  sessionUsage: SessionUsage | null
  contextWindow: number | null
  contextTokens: number | null
  // When false, the popover only shows this turn's stats (session totals
  // and context-window bar are hidden). Non-last turns pass false so the
  // chip describes that turn alone; the last turn passes true to surface
  // conversation totals.
  showSessionView?: boolean
}

export function TokenUsageBar({
  turnUsage,
  sessionUsage,
  contextWindow,
  contextTokens,
  showSessionView = true,
}: TokenUsageBarProps) {
  const t = useTranslations('chat')
  const [isExpanded, setIsExpanded] = useState(false)
  const showSession = showSessionView && sessionUsage !== null

  if (!turnUsage && !showSession) return null

  // Cache hit rate is cached / (uncached input + cached). input_tokens is the
  // UNCACHED portion, so dividing by it alone overcounts and can exceed 100%
  // on heavily-cached turns. Use the shared helper (denominator = input +
  // cacheRead), matching the admin cost insights.
  const cacheHitRate = turnUsage
    ? computeCacheHitRate({ input: turnUsage.input_tokens, cacheRead: turnUsage.cache_read_tokens })
    : null

  const sessionCacheHitRate = sessionUsage
    ? computeCacheHitRate({
        input: sessionUsage.total_input_tokens,
        cacheRead: sessionUsage.total_cache_read_tokens,
      })
    : null

  // context_tokens = max(input_tokens) across LLM calls in the last turn, sent by
  // the backend. Each call already carries the full context, so the largest single
  // call is the actual context size. Fall back to null (hide the bar) when not yet
  // available (old sessions without the field).
  const ctxPct =
    contextTokens != null && contextWindow && contextWindow > 0
      ? (contextTokens / contextWindow) * 100
      : null

  return (
    <Popover open={isExpanded} onOpenChange={setIsExpanded}>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-label={t('tokenUsage')}
            className="group/chip inline-flex items-center gap-1.5 rounded-md px-2 py-1
              text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60
              transition-colors"
          >
            <BarChart3 aria-hidden className="size-3.5" />
            <span className="hidden group-hover/chip:inline">{t('tokenUsage')}</span>
          </button>
        }
      />
      <PopoverContent
        align="start"
        sideOffset={8}
        className="w-72 gap-0 space-y-3 border border-border px-3 py-2.5 text-xs
          text-muted-foreground"
      >
        {turnUsage && (
          <div>
            <div className="font-medium text-foreground/70 mb-1">{t('turnLabel')}</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
              <span>{t('inputTokens')}</span>
              <span className="text-right font-mono tabular-nums">
                {formatTokenCount(turnUsage.input_tokens)}
              </span>
              <span>{t('outputTokens')}</span>
              <span className="text-right font-mono tabular-nums">
                {formatTokenCount(turnUsage.output_tokens)}
              </span>
              <span>{t('cacheHitRate')}</span>
              <span className="text-right font-mono tabular-nums">
                {formatPercent(cacheHitRate, 1)}
              </span>
            </div>
          </div>
        )}

        {showSession && sessionUsage && (
          <div>
            <div className="font-medium text-foreground/70 mb-1">{t('sessionLabel')}</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
              <span>{t('totalTokens')}</span>
              <span className="text-right font-mono tabular-nums">
                {formatTokenCount(
                  sessionUsage.total_input_tokens + sessionUsage.total_output_tokens,
                )}
              </span>
              <span>{t('cacheHitRate')}</span>
              <span className="text-right font-mono tabular-nums">
                {formatPercent(sessionCacheHitRate, 1)}
              </span>
            </div>
            {ctxPct !== null && (
              <div className="mt-1.5">
                <div className="flex items-center justify-between mb-0.5">
                  <span>{t('contextWindow')}</span>
                  <span className="font-mono tabular-nums">{ctxPct.toFixed(1)}%</span>
                </div>
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${progressColor(ctxPct)}`}
                    style={{ width: `${Math.min(ctxPct, 100)}%` }}
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
