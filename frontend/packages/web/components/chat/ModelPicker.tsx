'use client'

import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { AlertCircle, Box, Check, ChevronDown, Cpu, Loader2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { EffortSlider } from '@/components/chat/EffortSlider'
import { ModelBrandLogo } from '@/components/models/ModelBrandLogo'
import { fetchWorkspaceModelPresets } from '@/lib/api/presets'
import { formatContextWindow } from '@/lib/models/format-context-window'
import { inferModelBrand, modelIdFromPrimary } from '@/lib/models/infer-model-brand'
import { getPresetSelectionStore } from '@/lib/stores/preset-selection'
import { useAdminAccess } from '@/hooks/useAdminAccess'
import type { ModelTier, ThinkingLevel, WorkspacePresetSummary } from '@/lib/types/presets'
import { cn } from '@/lib/utils'

const PROVIDERS_ADMIN_PATH = '/admin/models'
const PRESETS_ADMIN_PATH = '/admin/presets'

interface ModelPickerProps {
  wsId: string
  /** Controlled open (slash `/model` / `/effort`). Uncontrolled when omitted. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
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
 * single button + popover.
 *
 * - Trigger shows the resolved **model** display name (not tier Max/Pro).
 * - List rows keep the **tier / custom key** as the primary label, with model
 *   name secondary (`Pro · Claude Opus 4.7`).
 * - Selection is stored and sent as the preset **key** (tier name or custom
 *   label). When admin remaps a tier's primary model, the same key resolves to
 *   the new model without the user re-picking.
 *
 * Hover Tooltip holds provider/model details. Backed by the per-`wsId` Zustand
 * store; refetches + revalidates on mount.
 */
export function ModelPicker({ wsId, open, onOpenChange }: ModelPickerProps): React.ReactElement {
  const t = useTranslations('chat')
  const tTier = useTranslations('adminPresets.modelTiers')
  const useStore = useMemo(() => getPresetSelectionStore(wsId), [wsId])
  const presets = useStore((s) => s.presets)
  const presetFetchStatus = useStore((s) => s.presetFetchStatus)
  const presetFetchError = useStore((s) => s.presetFetchError)
  const modelKey = useStore((s) => s.modelKey)
  const thinking = useStore((s) => s.thinking)
  const setPresets = useStore((s) => s.setPresets)
  const setPresetFetchState = useStore((s) => s.setPresetFetchState)
  const setModelKey = useStore((s) => s.setModelKey)
  const setThinking = useStore((s) => s.setThinking)
  const requestIdRef = useRef(0)

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

  const reloadPresets = useCallback(async () => {
    const requestId = ++requestIdRef.current
    setPresetFetchState('loading')
    try {
      const fresh = await fetchWorkspaceModelPresets(wsId)
      if (requestId !== requestIdRef.current) return
      setPresets(fresh)
      const valid = new Set(fresh.map((p) => p.key))
      const current = useStore.getState().modelKey
      if (current !== null && !valid.has(current)) setModelKey(null)
      setPresetFetchState('ready')
    } catch (error) {
      if (requestId !== requestIdRef.current) return
      const message = error instanceof Error ? error.message : null
      // Preserve last-known-good selections: a transient request failure must
      // not turn a working composer into a blank model picker.
      setPresetFetchState('error', message)
    }
  }, [wsId, setPresets, setPresetFetchState, setModelKey, useStore])

  useEffect(() => {
    void reloadPresets()
    return () => {
      requestIdRef.current += 1
    }
  }, [reloadPresets])

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
  /** List / a11y label: tier i18n name or custom preset key. */
  const nameOf = (p: WorkspacePresetSummary): string =>
    p.kind === 'tier' ? tierName[p.key as ModelTier] : p.key
  /** Trigger label: resolved model display name (not the tier). */
  const modelNameOf = (p: WorkspacePresetSummary): string =>
    p.model_display_name ?? p.model_id ?? modelIdFromPrimary(p.primary) ?? p.primary
  const descOf = (p: WorkspacePresetSummary): string =>
    p.kind === 'tier' ? tierDesc[p.key as ModelTier] : p.description

  const defaultPreset = presets.find((p) => p.is_default) ?? null
  const effectiveKey = modelKey ?? defaultPreset?.key ?? null
  const selected = presets.find((p) => p.key === effectiveKey) ?? null
  const selectedBrand = selected ? brandForPreset(selected) : null
  const selectedModelName = selected ? modelNameOf(selected) : null
  const confirmedEmpty = presetFetchStatus === 'ready' && presets.length === 0
  const loadingWithoutCache = presetFetchStatus === 'loading' && presets.length === 0

  const triggerAria =
    selected && selectedModelName
      ? `${t('modelPickerAria')}: ${selectedModelName} (${selected.primary})`
      : confirmedEmpty
        ? `${t('modelPickerAria')}: ${t('modelPicker.noModel')}`
        : t('modelPickerAria')

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger
        aria-label={triggerAria}
        data-testid="model-picker-trigger"
        className={cn(
          // Match composer control height (size-7). leading-none + slight
          // translate keeps label glyphs optically lower in the hover chip.
          'flex h-7 items-center gap-1.5 rounded-md border border-transparent bg-transparent px-2',
          'text-sm leading-none whitespace-nowrap transition-colors outline-none',
          // Borderless until hovered / open, so it blends into the composer
          // instead of reading as a framed control inside the input box.
          'hover:border-border hover:bg-accent',
          'aria-expanded:border-border aria-expanded:bg-accent',
          'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
        )}
      >
        {mounted && selected ? (
          <ModelBrandLogo brand={selectedBrand} label={selectedModelName ?? selected.primary} />
        ) : loadingWithoutCache ? (
          <Loader2 aria-hidden className="size-3.5 animate-spin text-muted-foreground" />
        ) : (
          <Cpu aria-hidden className="size-3.5 text-muted-foreground" />
        )}
        {/* Gated on client hydration too: with the presets cache persisted,
            `selected` resolves on the first client render, but the server
            rendered nothing — so defer to post-hydration to avoid a mismatch. */}
        {mounted && selected ? (
          <>
            <span className="translate-y-px font-medium">{modelNameOf(selected)}</span>
            <span aria-hidden className="translate-y-px text-muted-foreground/60">
              ·
            </span>
          </>
        ) : null}
        {mounted && loadingWithoutCache ? (
          <span className="translate-y-px text-muted-foreground">{t('modelPicker.loading')}</span>
        ) : null}
        {mounted && confirmedEmpty ? (
          <span className="translate-y-px text-muted-foreground">{t('modelPicker.noModel')}</span>
        ) : null}
        {mounted && !confirmedEmpty && !loadingWithoutCache ? (
          <span className="translate-y-px text-muted-foreground">
            {t(THINKING_LABEL_KEY[thinking])}
          </span>
        ) : null}
        <ChevronDown aria-hidden className="size-3.5 translate-y-px text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={6} className="w-72 p-0">
        <div className="px-2 pt-2 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          {t('modelSectionLabel')}
        </div>
        {confirmedEmpty ? (
          <ModelSetupHint />
        ) : presetFetchStatus === 'error' && presets.length === 0 ? (
          <ModelLoadError error={presetFetchError} onRetry={reloadPresets} />
        ) : (
          <>
            {presetFetchStatus === 'error' ? (
              <div className="mx-2 mb-1.5 flex items-center justify-between gap-2 rounded-md bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
                <span className="min-w-0 truncate">{t('modelPicker.cachedLoadFailed')}</span>
                <button
                  type="button"
                  onClick={() => void reloadPresets()}
                  className="shrink-0 underline underline-offset-2 hover:text-destructive/80"
                >
                  {t('modelPicker.retry')}
                </button>
              </div>
            ) : null}
            <TooltipProvider delay={300}>
              <div className="max-h-64 overflow-y-auto px-1.5 pb-1.5">
                {presets.map((p) => {
                  const active = p.key === effectiveKey
                  const label = nameOf(p)
                  const modelName = modelNameOf(p)
                  const brand = brandForPreset(p)
                  // Selection is always p.key (tier/custom), not the display name.
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
                        <ModelBrandLogo brand={brand} label={label} />
                        <span className="min-w-0 flex-1 truncate text-sm">
                          <span className="font-medium">{label}</span>
                          <span aria-hidden className="mx-1 text-muted-foreground/60">
                            ·
                          </span>
                          <span className="text-muted-foreground">{modelName}</span>
                        </span>
                        {p.is_default && (
                          <Badge variant="secondary" className="shrink-0 px-1 text-[10px]">
                            {t('defaultPresetBadge')}
                          </Badge>
                        )}
                        <Check
                          aria-hidden
                          className={cn(
                            'size-3.5 shrink-0',
                            active ? 'text-primary' : 'text-transparent',
                          )}
                        />
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
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}

function ModelSetupHint(): React.ReactElement {
  const t = useTranslations('chat.modelPicker')
  const { isAdmin, loading } = useAdminAccess()

  return (
    <div className="flex flex-col items-center px-5 py-6 text-center">
      <Box aria-hidden className="mb-2 size-7 text-muted-foreground/50" />
      <p className="text-sm font-medium">{t('emptyTitle')}</p>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t('emptyDescription')}</p>
      {loading ? null : isAdmin ? (
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <Link
            href={PROVIDERS_ADMIN_PATH}
            className="inline-flex h-7 items-center rounded border border-border bg-background px-2.5 text-sm font-medium hover:bg-muted"
          >
            {t('openProviders')}
          </Link>
          <Link
            href={PRESETS_ADMIN_PATH}
            className="inline-flex h-7 items-center rounded border border-border bg-background px-2.5 text-sm font-medium hover:bg-muted"
          >
            {t('openPresets')}
          </Link>
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">{t('contactAdmin')}</p>
      )}
    </div>
  )
}

function ModelLoadError({
  error,
  onRetry,
}: {
  error: string | null
  onRetry: () => Promise<void>
}): React.ReactElement {
  const t = useTranslations('chat.modelPicker')

  return (
    <div className="flex flex-col items-center px-5 py-6 text-center">
      <AlertCircle aria-hidden className="mb-2 size-7 text-destructive/70" />
      <p className="text-sm font-medium">{t('loadFailedTitle')}</p>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
        {t('loadFailedDescription')}
      </p>
      {error ? (
        <p className="mt-1 max-w-full truncate text-xs text-muted-foreground/70">{error}</p>
      ) : null}
      <Button variant="outline" size="sm" className="mt-4" onClick={() => void onRetry()}>
        {t('retry')}
      </Button>
    </div>
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
