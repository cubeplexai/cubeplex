'use client'

import { useEffect, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import {
  TURN_PREVIEW_FADE_MS,
  TURN_PREVIEW_HIDE_DELAY_MS,
  TURN_PREVIEW_SHOW_DELAY_MS,
  TURN_TICK_EASING,
  TURN_TICK_TRANSITION,
  previewSnippet,
  tickPresentation,
  type TurnNavItem,
} from '@/lib/turnNav'

interface TurnNavRailProps {
  turns: readonly TurnNavItem[]
  onJump: (turn: TurnNavItem) => void
  /** Older messages exist beyond the loaded window. */
  hasOlder?: boolean
  loadingOlder?: boolean
  onLoadEarlier?: () => void
}

/**
 * Left-edge turn ticks for the open conversation. Width and opacity
 * follow the pointer immediately. The preview card waits out a short
 * debounce so a quick pass along the column does not flash text.
 * A mark above the first tick loads the next older window.
 */
export function TurnNavRail({
  turns,
  onJump,
  hasOlder = false,
  loadingOlder = false,
  onLoadEarlier,
}: TurnNavRailProps) {
  const t = useTranslations('chat')
  const railRef = useRef<HTMLElement>(null)
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([])
  const showTimer = useRef<number | null>(null)
  const hideTimer = useRef<number | null>(null)
  const overPreviewRef = useRef(false)
  const [hoverIndex, setHoverIndex] = useState<number | null>(null)
  const [previewIndex, setPreviewIndex] = useState<number | null>(null)
  const [closing, setClosing] = useState(false)
  const [anchorTop, setAnchorTop] = useState(0)

  const clearShow = (): void => {
    if (showTimer.current != null) window.clearTimeout(showTimer.current)
    showTimer.current = null
  }

  const clearHide = (): void => {
    if (hideTimer.current != null) window.clearTimeout(hideTimer.current)
    hideTimer.current = null
  }

  useEffect(() => {
    return () => {
      if (showTimer.current != null) window.clearTimeout(showTimer.current)
      if (hideTimer.current != null) window.clearTimeout(hideTimer.current)
    }
  }, [])

  const measure = (index: number): number | null => {
    const rail = railRef.current
    const button = buttonRefs.current[index]
    if (!rail || !button) return null
    const railBox = rail.getBoundingClientRect()
    const buttonBox = button.getBoundingClientRect()
    const center = buttonBox.top - railBox.top + buttonBox.height / 2
    const halfCard = 64
    const min = Math.min(halfCard, railBox.height / 2)
    const max = Math.max(min, railBox.height - halfCard)
    return Math.min(max, Math.max(min, center))
  }

  const activate = (index: number): void => {
    clearShow()
    clearHide()
    setClosing(false)
    setHoverIndex(index)
    setPreviewIndex((current) => (current != null && current !== index ? null : current))
    showTimer.current = window.setTimeout(() => {
      const top = measure(index)
      if (top != null) setAnchorTop(top)
      setPreviewIndex(index)
      setClosing(false)
    }, TURN_PREVIEW_SHOW_DELAY_MS)
  }

  const scheduleHide = (): void => {
    setHoverIndex(null)
    clearShow()
    clearHide()
    hideTimer.current = window.setTimeout(() => {
      if (overPreviewRef.current) return
      setClosing(true)
      hideTimer.current = window.setTimeout(() => {
        setPreviewIndex(null)
        setClosing(false)
      }, TURN_PREVIEW_FADE_MS)
    }, TURN_PREVIEW_HIDE_DELAY_MS)
  }

  const holdPreview = (): void => {
    overPreviewRef.current = true
    clearHide()
    setClosing(false)
  }

  const leavePreview = (): void => {
    overPreviewRef.current = false
    scheduleHide()
  }

  const reposition = (): void => {
    if (previewIndex == null) return
    const top = measure(previewIndex)
    if (top != null) setAnchorTop(top)
  }

  if (turns.length === 0) return null

  const preview = previewIndex != null ? turns[previewIndex] : undefined
  const userPreview = preview ? previewSnippet(preview.userText) || t('turnNavAttachmentOnly') : ''
  const agentPreview = preview ? previewSnippet(preview.assistantText) || t('turnNavNoReply') : ''

  return (
    <nav
      ref={railRef}
      aria-label={t('turnNavLabel')}
      data-testid="turn-nav-rail"
      className="pointer-events-none absolute inset-y-3 left-0 z-20 hidden w-10 md:block"
    >
      <div className="flex h-full min-h-0 flex-col justify-center">
        <div
          className="pointer-events-auto flex max-h-full min-h-0 flex-col items-start gap-1.5 overflow-y-auto py-1 pl-1.5 scrollbar-none"
          onScroll={reposition}
        >
          {hasOlder && (
            <button
              type="button"
              data-testid="turn-nav-earlier"
              aria-label={t('turnNavLoadEarlier')}
              aria-busy={loadingOlder || undefined}
              disabled={loadingOlder || onLoadEarlier == null}
              className="group flex h-3 w-8 shrink-0 items-center disabled:cursor-default"
              onClick={() => onLoadEarlier?.()}
            >
              <span
                aria-hidden
                className="block size-1.5 -translate-y-px rotate-45 border-l border-t border-foreground opacity-40 transition-opacity duration-200 group-hover:opacity-100 group-disabled:opacity-40"
                style={{ transitionTimingFunction: TURN_TICK_EASING }}
              />
            </button>
          )}
          {turns.map((turn, index) => {
            const distance = hoverIndex == null ? null : Math.abs(index - hoverIndex)
            const presentation = tickPresentation(distance)
            const label = previewSnippet(turn.userText) || t('turnNavAttachmentOnly')
            return (
              <button
                key={turn.id}
                ref={(node) => {
                  buttonRefs.current[index] = node
                }}
                type="button"
                className="flex h-3 w-8 shrink-0 items-center"
                aria-label={t('turnNavJump', { preview: label })}
                aria-describedby={previewIndex === index ? 'turn-nav-preview' : undefined}
                onMouseEnter={() => activate(index)}
                onMouseLeave={scheduleHide}
                onFocus={() => activate(index)}
                onBlur={scheduleHide}
                onClick={() => onJump(turn)}
              >
                <span
                  data-testid="turn-nav-tick"
                  aria-hidden
                  className="block h-0.5 rounded-full bg-foreground"
                  style={{
                    width: presentation.width,
                    opacity: presentation.opacity,
                    transition: TURN_TICK_TRANSITION,
                  }}
                />
              </button>
            )
          })}
        </div>
      </div>
      {preview && (
        <div
          id="turn-nav-preview"
          role="tooltip"
          data-testid="turn-nav-preview"
          onMouseEnter={holdPreview}
          onMouseLeave={leavePreview}
          className={cn(
            'pointer-events-auto absolute left-8 z-30 w-72 -translate-y-1/2 rounded-lg border border-border bg-popover p-3 text-left shadow-lg',
            closing ? 'turn-nav-preview-out' : 'turn-nav-preview',
          )}
          style={{ top: anchorTop }}
        >
          <p className="text-xs text-muted-foreground">{t('turnNavYou')}</p>
          <p className="mt-0.5 text-sm text-popover-foreground">{userPreview}</p>
          <p className="mt-2 text-xs text-muted-foreground">{t('turnNavAgent')}</p>
          <p className="mt-0.5 text-sm text-muted-foreground">{agentPreview}</p>
        </div>
      )}
    </nav>
  )
}
