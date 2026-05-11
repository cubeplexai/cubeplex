'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { ChevronDown, ChevronRight, BarChart3 } from 'lucide-react'
import type { TurnUsage, SessionUsage } from '@cubebox/core'

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function progressColor(pct: number): string {
  if (pct >= 80) return 'bg-red-500'
  if (pct >= 50) return 'bg-amber-500'
  return 'bg-emerald-500'
}

interface TokenUsageBarProps {
  turnUsage: TurnUsage | null
  sessionUsage: SessionUsage | null
  contextWindow: number | null
}

export function TokenUsageBar({ turnUsage, sessionUsage, contextWindow }: TokenUsageBarProps) {
  const t = useTranslations('chat')
  const [isExpanded, setIsExpanded] = useState(false)

  if (!turnUsage && !sessionUsage) return null

  const cacheHitRate =
    turnUsage && turnUsage.input_tokens > 0
      ? (turnUsage.cache_read_tokens / turnUsage.input_tokens) * 100
      : null

  const ctxPct =
    sessionUsage && contextWindow && contextWindow > 0
      ? ((sessionUsage.total_input_tokens + sessionUsage.total_output_tokens) / contextWindow) * 100
      : null

  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex items-center gap-1.5 text-xs text-muted-foreground/60
          hover:text-muted-foreground transition-colors cursor-pointer"
      >
        <span className="text-muted-foreground/40">
          {isExpanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        </span>
        <BarChart3 className="size-3" />
        <span>{t('tokenUsage')}</span>
      </button>

      {isExpanded && (
        <div
          className="mt-2 text-xs text-muted-foreground bg-muted/30
            border border-border/50 rounded-lg px-3 py-2.5 space-y-3
            max-w-xs"
        >
          {turnUsage && (
            <div>
              <div className="font-medium text-foreground/70 mb-1">{t('turnLabel')}</div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                <span>{t('inputTokens')}</span>
                <span className="text-right font-mono">
                  {formatTokenCount(turnUsage.input_tokens)}
                </span>
                <span>{t('outputTokens')}</span>
                <span className="text-right font-mono">
                  {formatTokenCount(turnUsage.output_tokens)}
                </span>
                <span>{t('cacheHitRate')}</span>
                <span className="text-right font-mono">
                  {cacheHitRate !== null ? `${cacheHitRate.toFixed(1)}%` : '—'}
                </span>
              </div>
            </div>
          )}

          {sessionUsage && (
            <div>
              <div className="font-medium text-foreground/70 mb-1">{t('sessionLabel')}</div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                <span>{t('totalTokens')}</span>
                <span className="text-right font-mono">
                  {formatTokenCount(
                    sessionUsage.total_input_tokens + sessionUsage.total_output_tokens,
                  )}
                </span>
              </div>
              {ctxPct !== null && (
                <div className="mt-1.5">
                  <div className="flex items-center justify-between mb-0.5">
                    <span>{t('contextWindow')}</span>
                    <span className="font-mono">{ctxPct.toFixed(1)}%</span>
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
        </div>
      )}
    </div>
  )
}
