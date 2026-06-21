'use client'

import { useEffect, useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { Check, ChevronDown, Sparkles } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { EffortSlider } from '@/components/chat/EffortSlider'
import { fetchWorkspaceModelPresets } from '@/lib/api/presets'
import { getPresetSelectionStore } from '@/lib/stores/preset-selection'
import type { ModelTier, ThinkingLevel, WorkspacePresetSummary } from '@/lib/types/presets'
import { cn } from '@/lib/utils'

interface ModelPickerProps {
  wsId: string
}

const THINKING_LABEL_KEY = {
  off: 'thinkingLevelOff',
  low: 'thinkingLevelLow',
  medium: 'thinkingLevelMedium',
  high: 'thinkingLevelHigh',
  xhigh: 'thinkingLevelXhigh',
} as const satisfies Record<ThinkingLevel, string>

/**
 * Composer control that merges model-preset choice and thinking effort into a
 * single button + popover. The button shows "<model> · <effort>"; the popover
 * lists the workspace presets (with their descriptions) over an effort slider.
 * Backed by the per-`wsId` Zustand store; refetches + revalidates on mount.
 */
export function ModelPicker({ wsId }: ModelPickerProps): React.ReactElement {
  const t = useTranslations('chat')
  const tTier = useTranslations('adminPresets.modelTiers')
  const useStore = useMemo(() => getPresetSelectionStore(wsId), [wsId])
  const presets = useStore((s) => s.presets)
  const modelPresetKey = useStore((s) => s.modelPresetKey)
  const thinking = useStore((s) => s.thinking)
  const setPresets = useStore((s) => s.setPresets)
  const setModelPresetKey = useStore((s) => s.setModelPresetKey)
  const setThinking = useStore((s) => s.setThinking)

  useEffect(() => {
    let cancelled = false
    fetchWorkspaceModelPresets(wsId)
      .then((fresh) => {
        if (cancelled) return
        setPresets(fresh)
        const valid = new Set(fresh.map((p) => p.key))
        const current = useStore.getState().modelPresetKey
        if (current !== null && !valid.has(current)) setModelPresetKey(null)
      })
      .catch(() => {
        // Swallow — sending without preset_label means the workspace default.
      })
    return () => {
      cancelled = true
    }
  }, [wsId, setPresets, setModelPresetKey, useStore])

  // Built statically so next-intl's typed-key check sees every referenced key.
  const tierName: Record<ModelTier, string> = {
    lite: tTier('lite.name'),
    flash: tTier('flash.name'),
    pro: tTier('pro.name'),
    max: tTier('max.name'),
  }
  const tierDesc: Record<ModelTier, string> = {
    lite: tTier('lite.description'),
    flash: tTier('flash.description'),
    pro: tTier('pro.description'),
    max: tTier('max.description'),
  }
  const nameOf = (p: WorkspacePresetSummary): string =>
    p.kind === 'tier' ? tierName[p.key as ModelTier] : p.key
  const descOf = (p: WorkspacePresetSummary): string =>
    p.kind === 'tier' ? tierDesc[p.key as ModelTier] : p.description

  const defaultPreset = presets.find((p) => p.is_default) ?? null
  const effectiveKey = modelPresetKey ?? defaultPreset?.key ?? null
  const selected = presets.find((p) => p.key === effectiveKey) ?? null
  const modelLabel = selected ? nameOf(selected) : t('presetPlaceholder')

  return (
    <Popover>
      <PopoverTrigger
        aria-label={t('modelPickerAria')}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded border border-input bg-transparent px-2.5',
          'text-sm whitespace-nowrap transition-colors outline-none hover:bg-accent',
          'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
        )}
      >
        <Sparkles aria-hidden className="size-3.5 text-muted-foreground" />
        <span className="font-medium">{modelLabel}</span>
        <span aria-hidden className="text-muted-foreground/60">
          ·
        </span>
        <span className="text-muted-foreground">{t(THINKING_LABEL_KEY[thinking])}</span>
        <ChevronDown aria-hidden className="size-3.5 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={6} className="w-72 p-0">
        <div className="px-2 pt-2 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          {t('modelSectionLabel')}
        </div>
        <div className="max-h-64 overflow-y-auto px-1.5 pb-1.5">
          {presets.map((p) => {
            const active = p.key === effectiveKey
            return (
              <button
                key={p.key}
                type="button"
                onClick={() => setModelPresetKey(p.key)}
                aria-pressed={active}
                className={cn(
                  'flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors',
                  active ? 'bg-accent' : 'hover:bg-accent/60',
                )}
              >
                <Check
                  aria-hidden
                  className={cn(
                    'mt-0.5 size-3.5 shrink-0',
                    active ? 'text-primary' : 'text-transparent',
                  )}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="text-sm font-medium">{nameOf(p)}</span>
                    {p.is_default && (
                      <Badge variant="secondary" className="px-1 text-[10px]">
                        {t('defaultPresetBadge')}
                      </Badge>
                    )}
                  </span>
                  {descOf(p) ? (
                    <span className="mt-0.5 block text-xs text-muted-foreground">{descOf(p)}</span>
                  ) : null}
                </span>
              </button>
            )
          })}
        </div>
        <div className="border-t border-border p-3">
          <EffortSlider value={thinking} onChange={setThinking} />
        </div>
      </PopoverContent>
    </Popover>
  )
}
