'use client'

import { useRef } from 'react'
import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'
import type { ThinkingLevel } from '@/lib/types/presets'

const LEVELS = [
  { value: 'off', labelKey: 'thinkingLevelOff' },
  { value: 'low', labelKey: 'thinkingLevelLow' },
  { value: 'medium', labelKey: 'thinkingLevelMedium' },
  { value: 'high', labelKey: 'thinkingLevelHigh' },
  { value: 'xhigh', labelKey: 'thinkingLevelXhigh' },
] as const satisfies readonly { value: ThinkingLevel; labelKey: string }[]

const MAX_INDEX = LEVELS.length - 1

interface EffortSliderProps {
  value: ThinkingLevel
  onChange: (value: ThinkingLevel) => void
}

/**
 * Discrete "Faster → Smarter" effort slider over the five thinking levels.
 * Click/drag anywhere on the track snaps to the nearest stop; ArrowLeft/Right
 * (and Home/End) step it. The whole track is the slider (role="slider"); the
 * dots are visual stops.
 */
export function EffortSlider({ value, onChange }: EffortSliderProps): React.ReactElement {
  const t = useTranslations('chat')
  const trackRef = useRef<HTMLDivElement>(null)
  const index = Math.max(
    0,
    LEVELS.findIndex((l) => l.value === value),
  )
  const currentLabel = t(LEVELS[index].labelKey)

  const setByIndex = (i: number): void => {
    const clamped = Math.min(MAX_INDEX, Math.max(0, i))
    onChange(LEVELS[clamped].value)
  }

  const setFromClientX = (clientX: number): void => {
    const el = trackRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    // The dots sit inside an 8px horizontal inset (px-2), so map within it.
    const pad = 8
    const usable = Math.max(1, rect.width - pad * 2)
    const ratio = (clientX - rect.left - pad) / usable
    setByIndex(Math.round(ratio * MAX_INDEX))
  }

  return (
    <div className="select-none">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{t('effortLabel')}</span>
        <span className="text-xs font-semibold text-foreground">{currentLabel}</span>
      </div>

      <div
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-label={t('effortLabel')}
        aria-valuemin={0}
        aria-valuemax={MAX_INDEX}
        aria-valuenow={index}
        aria-valuetext={currentLabel}
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
            e.preventDefault()
            setByIndex(index - 1)
          } else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
            e.preventDefault()
            setByIndex(index + 1)
          } else if (e.key === 'Home') {
            e.preventDefault()
            setByIndex(0)
          } else if (e.key === 'End') {
            e.preventDefault()
            setByIndex(MAX_INDEX)
          }
        }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          setFromClientX(e.clientX)
        }}
        onPointerMove={(e) => {
          if (e.buttons === 1) setFromClientX(e.clientX)
        }}
        className="relative mt-2 h-6 cursor-pointer rounded outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        {/* base rail */}
        <div className="absolute inset-x-2 top-1/2 h-1 -translate-y-1/2 rounded-full bg-muted" />
        {/* filled rail up to the thumb */}
        <div
          className="absolute top-1/2 left-2 h-1 -translate-y-1/2 rounded-full bg-primary"
          style={{ width: `calc((100% - 16px) * ${index / MAX_INDEX})` }}
        />
        {/* stops + thumb */}
        <div className="relative flex h-full items-center justify-between px-2">
          {LEVELS.map((lvl, i) => (
            <span key={lvl.value} aria-hidden className="flex size-5 items-center justify-center">
              {i === index ? (
                <span className="size-3.5 rounded-full border-2 border-primary bg-background shadow-sm" />
              ) : (
                <span
                  className={cn(
                    'size-1.5 rounded-full',
                    i < index ? 'bg-primary' : 'bg-muted-foreground/40',
                  )}
                />
              )}
            </span>
          ))}
        </div>
      </div>

      <div className="mt-1 flex items-center justify-between px-0.5 text-[10px] text-muted-foreground">
        <span>{t('effortFaster')}</span>
        <span>{t('effortSmarter')}</span>
      </div>
    </div>
  )
}
