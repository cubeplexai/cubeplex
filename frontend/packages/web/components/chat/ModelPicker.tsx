'use client'

import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { useTranslations } from 'next-intl'
import { Check, ChevronDown, Cpu } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { EffortSlider } from '@/components/chat/EffortSlider'
import { ModelBrandLogo } from '@/components/models/ModelBrandLogo'
import { fetchWorkspaceModelPresets } from '@/lib/api/presets'
import { formatContextWindow } from '@/lib/models/format-context-window'
import { inferModelBrand, modelIdFromPrimary } from '@/lib/models/infer-model-brand'
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
  max: 'thinkingLevelMax',
} as const satisfies Record<ThinkingLevel, string>

function brandForPreset(p: WorkspacePresetSummary): string | null {
  const modelId = p.model_id ?? modelIdFromPrimary(p.primary)
  return inferModelBrand(modelId, p.model_display_name)
}

/**
 * Composer control that merges model-preset choice and thinking effort into a
 * single button + popover. List rows show model-family brand + tier/custom
 * label; hover Tooltip holds provider/model details. Backed by the per-`wsId`
 * Zustand store; refetches + revalidates on mount.
 */
export function ModelPicker({ wsId }: ModelPickerProps): React.ReactElement {
  const t = useTranslations('chat')
  const tTier = useTranslations('adminPresets.modelTiers')
  const useStore = useMemo(() => getPresetSelectionStore(wsId), [wsId])
  const presets = useStore((s) => s.presets)
  const modelKey = useStore((s) => s.modelKey)
  const thinking = useStore((s) => s.thinking)
  const setPresets = useStore((s) => s.setPresets)
  const setModelKey = useStore((s) => s.setModelKey)
  const setThinking = useStore((s) => s.setThinking)

  // The store's `thinking` is persisted; on the server it's the default
  // (medium). Gate its label on client hydration so the button never paints
  // the SSR default before the persisted value hydrates ("Medium" → "High").
  // useSyncExternalStore is the hydration-safe "are we on the client" read
  // (false on the server snapshot, true once hydrated) — no setState-in-effect.
  const mounted = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  )

  useEffect(() => {
    let cancelled = false
    fetchWorkspaceModelPresets(wsId)
      .then((fresh) => {
        if (cancelled) return
        setPresets(fresh)
        const valid = new Set(fresh.map((p) => p.key))
        const current = useStore.getState().modelKey
        if (current !== null && !valid.has(current)) setModelKey(null)
      })
      .catch(() => {
        // Swallow — sending without model_key means the workspace default.
      })
    return () => {
      cancelled = true
    }
  }, [wsId, setPresets, setModelKey, useStore])

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
  const effectiveKey = modelKey ?? defaultPreset?.key ?? null
  const selected = presets.find((p) => p.key === effectiveKey) ?? null
  const selectedBrand = selected ? brandForPreset(selected) : null
  const selectedLabel = selected ? nameOf(selected) : null

  const triggerAria =
    selected && selectedLabel
      ? `${t('modelPickerAria')}: ${selectedLabel} (${selected.primary})`
      : t('modelPickerAria')

  return (
    <Popover>
      <PopoverTrigger
        aria-label={triggerAria}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded border border-transparent bg-transparent px-2',
          'text-sm whitespace-nowrap transition-colors outline-none',
          // Borderless until hovered / open, so it blends into the composer
          // instead of reading as a framed control inside the input box.
          'hover:border-border hover:bg-accent',
          'aria-expanded:border-border aria-expanded:bg-accent',
          'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
        )}
      >
        {mounted && selected ? (
          <ModelBrandLogo brand={selectedBrand} label={selectedLabel ?? selected.primary} />
        ) : (
          <Cpu aria-hidden className="size-3.5 text-muted-foreground" />
        )}
        {/* Gated on client hydration too: with the presets cache persisted,
            `selected` resolves on the first client render, but the server
            rendered nothing — so defer to post-hydration to avoid a mismatch. */}
        {mounted && selected ? (
          <>
            <span className="font-medium">{nameOf(selected)}</span>
            <span aria-hidden className="text-muted-foreground/60">
              ·
            </span>
          </>
        ) : null}
        {mounted ? (
          <span className="text-muted-foreground">{t(THINKING_LABEL_KEY[thinking])}</span>
        ) : null}
        <ChevronDown aria-hidden className="size-3.5 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={6} className="w-72 p-0">
        <div className="px-2 pt-2 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          {t('modelSectionLabel')}
        </div>
        <TooltipProvider delay={300}>
          <div className="max-h-64 overflow-y-auto px-1.5 pb-1.5">
            {presets.map((p) => {
              const active = p.key === effectiveKey
              const label = nameOf(p)
              const brand = brandForPreset(p)
              const rowAria = `${label} · ${p.primary}`
              return (
                <Tooltip key={p.key}>
                  <TooltipTrigger
                    type="button"
                    onClick={() => setModelKey(p.key)}
                    aria-pressed={active}
                    aria-label={rowAria}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors',
                      active ? 'bg-accent' : 'hover:bg-accent/60',
                    )}
                  >
                    <Check
                      aria-hidden
                      className={cn(
                        'size-3.5 shrink-0',
                        active ? 'text-primary' : 'text-transparent',
                      )}
                    />
                    <ModelBrandLogo brand={brand} label={label} />
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{label}</span>
                    {p.is_default && (
                      <Badge variant="secondary" className="shrink-0 px-1 text-[10px]">
                        {t('defaultPresetBadge')}
                      </Badge>
                    )}
                  </TooltipTrigger>
                  <TooltipContent side="left" sideOffset={8} className="max-w-xs p-2.5">
                    <PresetTooltipBody preset={p} description={descOf(p)} t={t} />
                  </TooltipContent>
                </Tooltip>
              )
            })}
          </div>
        </TooltipProvider>
        <div className="border-t border-border p-3">
          <EffortSlider value={thinking} onChange={setThinking} />
        </div>
      </PopoverContent>
    </Popover>
  )
}

function PresetTooltipBody({
  preset: p,
  description,
  t,
}: {
  preset: WorkspacePresetSummary
  description: string
  t: ReturnType<typeof useTranslations<'chat'>>
}): React.ReactElement {
  const provider = p.provider_slug ?? p.primary.split('/')[0] ?? p.primary
  const modelId = p.model_id ?? modelIdFromPrimary(p.primary) ?? p.primary
  const ctx = formatContextWindow(p.context_window ?? null)
  const modalities = p.input_modalities?.length ? p.input_modalities.join(', ') : null

  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-left text-xs">
      <dt className="text-background/70">{t('modelTooltipProvider')}</dt>
      <dd className="min-w-0 truncate font-medium">{provider}</dd>

      <dt className="text-background/70">{t('modelTooltipModelId')}</dt>
      <dd className="min-w-0 break-all font-mono text-[11px] font-medium">{modelId}</dd>

      {p.model_display_name ? (
        <>
          <dt className="text-background/70">{t('modelTooltipDisplayName')}</dt>
          <dd className="min-w-0 truncate font-medium">{p.model_display_name}</dd>
        </>
      ) : null}

      {ctx ? (
        <>
          <dt className="text-background/70">{t('modelTooltipContext')}</dt>
          <dd className="font-medium">{ctx}</dd>
        </>
      ) : null}

      {p.reasoning != null ? (
        <>
          <dt className="text-background/70">{t('modelTooltipReasoning')}</dt>
          <dd className="font-medium">
            {p.reasoning ? t('modelTooltipReasoningYes') : t('modelTooltipReasoningNo')}
          </dd>
        </>
      ) : null}

      {modalities ? (
        <>
          <dt className="text-background/70">{t('modelTooltipModalities')}</dt>
          <dd className="min-w-0 font-medium">{modalities}</dd>
        </>
      ) : null}

      {description ? (
        <>
          <dt className="text-background/70">{t('modelTooltipDescription')}</dt>
          <dd className="min-w-0 font-medium leading-snug">{description}</dd>
        </>
      ) : null}
    </dl>
  )
}
