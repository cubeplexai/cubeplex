'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { ArrowDown, ArrowUp, Loader2, Plus, Trash2 } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { jsonHeaders } from '@/lib/csrf'
import { MODEL_TIERS, TASK_KEYS } from '@/lib/types/presets'
import type {
  CustomPreset,
  ModelPresetsConfig,
  ModelTier,
  TaskKey,
  TierSetting,
} from '@/lib/types/presets'
import type { AdminModelPresetsResponse } from '@/lib/api/presets'
import { AdminPageShell } from '@/components/management/AdminPageShell'
import { cn } from '@/lib/utils'

interface PresetEditorProps {
  initial: AdminModelPresetsResponse
  availableModels: string[]
}

const NOT_SET = '__not_set__'

// Static maps so next-intl's typed-key check + the i18n-key parity script can
// see exactly which message keys are referenced (no dynamic `t(MAP[key])`).
const TIER_NAME_KEY = {
  lite: 'modelTiers.lite.name',
  flash: 'modelTiers.flash.name',
  pro: 'modelTiers.pro.name',
  max: 'modelTiers.max.name',
} as const
const TIER_DESC_KEY = {
  lite: 'modelTiers.lite.description',
  flash: 'modelTiers.flash.description',
  pro: 'modelTiers.pro.description',
  max: 'modelTiers.max.description',
} as const
const TASK_NAME_KEY = {
  title: 'taskRouting.title.name',
  summarize: 'taskRouting.summarize.name',
  compaction: 'taskRouting.compaction.name',
} as const
const TASK_HINT_KEY = {
  title: 'taskRouting.title.hint',
  summarize: 'taskRouting.summarize.hint',
  compaction: 'taskRouting.compaction.hint',
} as const

interface ApiError {
  status: string
  error_code?: string
  message?: string
  details?: string
  data?: unknown
}

export function extractMissingRefs(data: unknown): string[] {
  if (typeof data !== 'object' || data === null) return []
  const refs = (data as { missing_refs?: unknown }).missing_refs
  if (!Array.isArray(refs)) return []
  return refs.filter((r): r is string => typeof r === 'string')
}

// Fallback: backend BrokenPresetError pre-data-field serialized missing_refs
// as `details="missing_refs=['a/b', 'c/d']"`. Parse it back into a string[].
export function parseMissingRefs(details: string | undefined): string[] {
  if (!details) return []
  const match = details.match(/missing_refs=\[(.*)\]/)
  if (!match) return []
  const inner = match[1].trim()
  if (!inner) return []
  return inner
    .split(',')
    .map((s) => s.trim().replace(/^['"]|['"]$/g, ''))
    .filter(Boolean)
}

function emptyTier(): TierSetting {
  return { enabled: false, primary: null, fallbacks: [] }
}

function emptyConfig(): ModelPresetsConfig {
  return {
    tiers: {
      lite: emptyTier(),
      flash: emptyTier(),
      pro: emptyTier(),
      max: emptyTier(),
    },
    custom_presets: [],
    default_preset: 'pro',
    task_routing: {},
  }
}

// Canonical serialization for dirty-checking: tiers in lite/flash/pro/max
// order, custom presets by index, then default_preset, then task_routing in
// TASK_KEYS order so re-adding a key in a different order doesn't read as edit.
function canonicalize(c: ModelPresetsConfig): string {
  const tiers = MODEL_TIERS.map((t) => ({
    tier: t,
    enabled: c.tiers[t].enabled,
    primary: c.tiers[t].primary,
    fallbacks: c.tiers[t].fallbacks,
  }))
  const tasks: Partial<Record<TaskKey, string>> = {}
  for (const k of TASK_KEYS) {
    if (c.task_routing[k]) tasks[k] = c.task_routing[k]
  }
  return JSON.stringify({
    tiers,
    custom_presets: c.custom_presets.map((p) => ({
      label: p.label,
      primary: p.primary,
      fallbacks: p.fallbacks,
      description: p.description,
    })),
    default_preset: c.default_preset,
    task_routing: tasks,
  })
}

/**
 * Move an array entry from index `from` to index `to`. `to` is the index the
 * moved item should occupy in the final array. Exported for unit tests.
 */
export function reorder<T>(arr: readonly T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= arr.length || to >= arr.length) {
    return [...arr]
  }
  const next = [...arr]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

// The model-id part of a `slug/model_id` ref (used for the optional task-row
// secondary label). Returns the whole ref if it has no slash.
function modelIdOf(ref: string): string {
  const idx = ref.indexOf('/')
  return idx === -1 ? ref : ref.slice(idx + 1)
}

export function PresetEditor({ initial, availableModels }: PresetEditorProps): React.ReactElement {
  const t = useTranslations('adminPresets')
  const [body, setBody] = useState<ModelPresetsConfig>(() => initial.value ?? emptyConfig())
  const [savedBody, setSavedBody] = useState<ModelPresetsConfig>(
    () => initial.value ?? emptyConfig(),
  )
  const [origin, setOrigin] = useState(initial.origin)
  const [saving, setSaving] = useState(false)
  const [banner, setBanner] = useState<string | null>(null)
  const [missingRefs, setMissingRefs] = useState<Set<string>>(new Set())

  // ----- tier mutations -----
  const updateTier = (tier: ModelTier, patch: Partial<TierSetting>): void => {
    setBody((b) => ({ ...b, tiers: { ...b.tiers, [tier]: { ...b.tiers[tier], ...patch } } }))
  }

  // ----- custom-preset mutations -----
  const updateCustom = (idx: number, patch: Partial<CustomPreset>): void => {
    setBody((b) => ({
      ...b,
      custom_presets: b.custom_presets.map((p, i) => (i === idx ? { ...p, ...patch } : p)),
    }))
  }

  const addCustom = (): void => {
    setBody((b) => ({
      ...b,
      custom_presets: [
        ...b.custom_presets,
        { label: '', primary: '', fallbacks: [], description: '' },
      ],
    }))
  }

  const removeCustom = (idx: number): void => {
    setBody((b) => {
      const removedLabel = b.custom_presets[idx]?.label.trim()
      const next = b.custom_presets.filter((_, i) => i !== idx)
      // Drop task_routing values that pointed at the removed label.
      const routing = { ...b.task_routing }
      if (removedLabel) {
        for (const k of TASK_KEYS) {
          if (routing[k] === removedLabel) delete routing[k]
        }
      }
      // Clear default if it pointed at the removed preset.
      const defaultPreset = b.default_preset === removedLabel ? 'pro' : b.default_preset
      return { ...b, custom_presets: next, task_routing: routing, default_preset: defaultPreset }
    })
  }

  const setTaskRouting = (key: TaskKey, value: string | undefined): void => {
    setBody((b) => {
      const next: Partial<Record<TaskKey, string>> = { ...b.task_routing }
      if (!value) delete next[key]
      else next[key] = value
      return { ...b, task_routing: next }
    })
  }

  // The keys selectable from task routing + default radio: enabled tiers with a
  // primary, plus custom presets with a non-empty label.
  const availableKeys = useMemo(
    () => [
      ...MODEL_TIERS.filter((tier) => body.tiers[tier].enabled && body.tiers[tier].primary),
      ...body.custom_presets.map((c) => c.label.trim()).filter(Boolean),
    ],
    [body.tiers, body.custom_presets],
  )

  const primaryByKey = useMemo(() => {
    const map = new Map<string, string>()
    for (const tier of MODEL_TIERS) {
      const s = body.tiers[tier]
      if (s.enabled && s.primary) map.set(tier, s.primary)
    }
    for (const c of body.custom_presets) {
      const label = c.label.trim()
      if (label && c.primary) map.set(label, c.primary)
    }
    return map
  }, [body.tiers, body.custom_presets])

  const tierNameSet = useMemo(() => new Set<string>(MODEL_TIERS), [])

  const duplicateLabels = useMemo(() => {
    const seen = new Set<string>()
    const dups = new Set<string>()
    for (const c of body.custom_presets) {
      const norm = c.label.trim()
      if (!norm) continue
      if (seen.has(norm)) dups.add(norm)
      else seen.add(norm)
    }
    return dups
  }, [body.custom_presets])

  // ----- save-gating validation (cheap; computed every render) -----
  const computeValidationError = (): string | null => {
    for (const tier of MODEL_TIERS) {
      const s = body.tiers[tier]
      if (s.enabled && !s.primary) return t('errorTierMissingPrimary')
    }
    for (const c of body.custom_presets) {
      const label = c.label.trim()
      if (!label) return t('errorCustomMissingLabel')
      if (tierNameSet.has(label)) return t('errorLabelCollidesTier')
      if (!c.primary) return t('errorCustomMissingPrimary')
    }
    if (duplicateLabels.size > 0) return t('errorDuplicateLabel')
    if (!availableKeys.includes(body.default_preset)) return t('errorDefaultUnavailable')
    return null
  }
  const validationError = computeValidationError()

  const discard = (): void => {
    setBody(savedBody)
    setBanner(null)
    setMissingRefs(new Set())
  }

  const handleSave = async (): Promise<void> => {
    setSaving(true)
    setBanner(null)
    setMissingRefs(new Set())

    try {
      const res = await fetch('/api/v1/admin/model-presets', {
        method: 'PUT',
        credentials: 'include',
        headers: jsonHeaders(),
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const data: ApiError = await res.json().catch(() => ({ status: 'error' }))
        if (data.error_code === 'broken_preset') {
          let refs = extractMissingRefs(data.data)
          if (refs.length === 0) {
            refs = parseMissingRefs(data.details)
          }
          setMissingRefs(new Set(refs))
          setBanner(t('errorBrokenPreset'))
        } else {
          setBanner(data.message ?? t('errorGeneric', { status: res.status }))
        }
        return
      }
      const data = (await res.json()) as AdminModelPresetsResponse
      const saved = data.value ?? emptyConfig()
      setBody(saved)
      setSavedBody(saved)
      setOrigin(data.origin)
    } catch (err) {
      setBanner((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const dirty = canonicalize(body) !== canonicalize(savedBody)

  return (
    <AdminPageShell
      title={t('title')}
      description={
        <>
          {t('subtitle')}
          {origin !== 'org' ? (
            <span className="ml-2 rounded-full bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
              {origin === 'system' ? t('originSystem') : t('originNone')}
            </span>
          ) : null}
        </>
      }
    >
      {banner && (
        <Alert variant="destructive" role="alert">
          <AlertTitle>{t('errorTitle')}</AlertTitle>
          <AlertDescription>{banner}</AlertDescription>
        </Alert>
      )}

      <RadioGroup
        value={body.default_preset}
        onValueChange={(v) => setBody((b) => ({ ...b, default_preset: v }))}
        className="contents"
      >
        {/* ---------- Section 1: Tiers ---------- */}
        <section aria-label={t('tiersSectionHeading')} className="space-y-3">
          <h3 className="text-sm font-medium">{t('tiersSectionHeading')}</h3>
          {MODEL_TIERS.map((tier) => {
            const setting = body.tiers[tier]
            const selectable = setting.enabled && !!setting.primary
            return (
              <div
                key={tier}
                className="rounded-lg border border-border/70 bg-card/40 p-4"
                data-testid={`tier-row-${tier}`}
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold">{t(TIER_NAME_KEY[tier])}</div>
                    <p className="mt-0.5 text-xs text-muted-foreground">{t(TIER_DESC_KEY[tier])}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-4">
                    <DefaultRadio value={tier} disabled={!selectable} t={t} />
                    <div className="flex items-center gap-2">
                      <Switch
                        id={`tier-enabled-${tier}`}
                        checked={setting.enabled}
                        onCheckedChange={(c: boolean) => updateTier(tier, { enabled: c })}
                      />
                      <Label
                        htmlFor={`tier-enabled-${tier}`}
                        className="cursor-pointer text-xs text-muted-foreground"
                      >
                        {t('tierEnabled')}
                      </Label>
                    </div>
                  </div>
                </div>

                {setting.enabled && (
                  <div className="mt-4 border-t border-border/60 pt-4">
                    <PrimaryFallbackEditor
                      primary={setting.primary}
                      fallbacks={setting.fallbacks}
                      availableModels={availableModels}
                      missingRefs={missingRefs}
                      onPrimaryChange={(ref) => updateTier(tier, { primary: ref })}
                      onFallbacksChange={(next) => updateTier(tier, { fallbacks: next })}
                    />
                  </div>
                )}
              </div>
            )
          })}
        </section>

        {/* ---------- Section 2: Custom presets ---------- */}
        <section aria-label={t('customSectionHeading')} className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium">{t('customSectionHeading')}</h3>
            <Button variant="outline" size="sm" onClick={addCustom}>
              <Plus className="size-3.5" />
              <span>{t('addCustom')}</span>
            </Button>
          </div>

          {body.custom_presets.map((preset, idx) => {
            const label = preset.label.trim()
            const isEmptyLabel = label.length === 0
            const isDuplicate = duplicateLabels.has(label)
            const collidesTier = label.length > 0 && tierNameSet.has(label)
            const labelInvalid = isEmptyLabel || isDuplicate || collidesTier
            const selectable = !isEmptyLabel && !isDuplicate && !collidesTier && !!preset.primary
            return (
              <div
                key={idx}
                className="rounded-lg border border-border/70 bg-card/40 p-4"
                data-testid={`custom-row-${idx}`}
              >
                <div className="flex items-start gap-4">
                  <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
                    <div>
                      <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
                        {t('customLabel')}
                      </Label>
                      <Input
                        value={preset.label}
                        onChange={(e) => updateCustom(idx, { label: e.target.value })}
                        placeholder={t('labelPlaceholder')}
                        aria-invalid={labelInvalid || undefined}
                        className={cn(labelInvalid && 'border-destructive')}
                      />
                      {isDuplicate && (
                        <p className="mt-1 text-xs text-destructive">{t('errorDuplicateLabel')}</p>
                      )}
                      {collidesTier && (
                        <p className="mt-1 text-xs text-destructive">
                          {t('errorLabelCollidesTier')}
                        </p>
                      )}
                    </div>
                    <div>
                      <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
                        {t('customDescription')}
                      </Label>
                      <Input
                        value={preset.description}
                        onChange={(e) => updateCustom(idx, { description: e.target.value })}
                      />
                    </div>
                  </div>

                  <div className="flex shrink-0 items-center gap-3 self-start pt-5">
                    <DefaultRadio value={label} disabled={!selectable} t={t} />
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('removePreset')}
                      onClick={() => removeCustom(idx)}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>

                <div className="mt-4 border-t border-border/60 pt-4">
                  <PrimaryFallbackEditor
                    primary={preset.primary}
                    fallbacks={preset.fallbacks}
                    availableModels={availableModels}
                    missingRefs={missingRefs}
                    onPrimaryChange={(ref) => updateCustom(idx, { primary: ref ?? '' })}
                    onFallbacksChange={(next) => updateCustom(idx, { fallbacks: next })}
                  />
                </div>
              </div>
            )
          })}
        </section>
      </RadioGroup>

      {/* ---------- Section 3: Task routing ---------- */}
      <section aria-label={t('taskRouting.heading')} className="space-y-1">
        <h3 className="text-sm font-medium">{t('taskRouting.heading')}</h3>
        <p className="pb-2 text-xs text-muted-foreground">{t('taskRouting.hint')}</p>
        <div className="divide-y divide-border/60 rounded-lg border border-border/70 bg-card/40 px-4">
          {TASK_KEYS.map((task) => (
            <div key={task} className="flex items-center justify-between gap-4 py-2.5">
              <div className="min-w-0">
                <div className="text-sm font-medium">{t(TASK_NAME_KEY[task])}</div>
                <div className="text-xs text-muted-foreground">{t(TASK_HINT_KEY[task])}</div>
              </div>
              <Select
                value={body.task_routing[task] ?? NOT_SET}
                onValueChange={(v) => setTaskRouting(task, !v || v === NOT_SET ? undefined : v)}
              >
                <SelectTrigger className="w-56" aria-label={t(TASK_NAME_KEY[task])}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NOT_SET}>
                    <span className="text-muted-foreground">
                      {t('taskRouting.useDefault', { preset: body.default_preset })}
                    </span>
                  </SelectItem>
                  {availableKeys.map((key) => {
                    const primary = primaryByKey.get(key)
                    return (
                      <SelectItem key={key} value={key}>
                        {key}
                        {primary ? (
                          <span className="ml-1.5 text-xs text-muted-foreground">
                            · {modelIdOf(primary)}
                          </span>
                        ) : null}
                      </SelectItem>
                    )
                  })}
                </SelectContent>
              </Select>
            </div>
          ))}
        </div>
      </section>

      {validationError && !banner && (
        <p className="text-xs text-destructive" role="alert">
          {validationError}
        </p>
      )}

      <div className="sticky bottom-0 -mx-1 flex items-center justify-end gap-2 rounded-lg border border-border/60 bg-background/95 px-3 py-2.5 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <Button variant="ghost" size="sm" onClick={discard} disabled={!dirty || saving}>
          {t('discard')}
        </Button>
        <Button
          size="sm"
          onClick={() => void handleSave()}
          disabled={!dirty || saving || validationError !== null}
          aria-label={t('save')}
        >
          {saving ? <Loader2 className="size-4 animate-spin" /> : null}
          <span>{t('save')}</span>
        </Button>
      </div>
    </AdminPageShell>
  )
}

interface DefaultRadioProps {
  value: string
  disabled: boolean
  t: ReturnType<typeof useTranslations>
}

// The default-preset radio cell shared by tier rows and custom rows. An empty
// value (custom preset with no label yet) is rendered disabled and never
// selectable.
function DefaultRadio({ value, disabled, t }: DefaultRadioProps): React.ReactElement {
  const id = `default-${value || 'unset'}`
  return (
    <div className="flex items-center gap-1.5">
      <RadioGroupItem value={value} id={id} disabled={disabled || !value} />
      <Label
        htmlFor={id}
        className={cn(
          'cursor-pointer text-xs text-muted-foreground',
          (disabled || !value) && 'cursor-not-allowed opacity-50',
        )}
      >
        {t('defaultBadge')}
      </Label>
    </div>
  )
}

interface PrimaryFallbackEditorProps {
  primary: string | null
  fallbacks: string[]
  availableModels: string[]
  missingRefs: Set<string>
  onPrimaryChange: (ref: string | null) => void
  onFallbacksChange: (next: string[]) => void
}

// A single "Primary model" ref picker plus the ordered "Fallbacks" list. The
// fallbacks list reuses ChainEditor (add + reorder + remove). DRY: both the
// primary picker and the fallbacks add-input share the RefPicker autocomplete.
function PrimaryFallbackEditor({
  primary,
  fallbacks,
  availableModels,
  missingRefs,
  onPrimaryChange,
  onFallbacksChange,
}: PrimaryFallbackEditorProps): React.ReactElement {
  const t = useTranslations('adminPresets')

  const moveFallback = (from: number, to: number): void => {
    if (to < 0 || to >= fallbacks.length) return
    onFallbacksChange(reorder(fallbacks, from, to))
  }
  const removeFallback = (idx: number): void => {
    onFallbacksChange(fallbacks.filter((_, i) => i !== idx))
  }
  const addFallback = (ref: string): void => {
    if (!ref || fallbacks.includes(ref) || ref === primary) return
    onFallbacksChange([...fallbacks, ref])
  }

  const primaryMissing = !!primary && missingRefs.has(primary)

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
          {t('primary')}
        </Label>
        {primary ? (
          <div
            className={cn(
              'flex items-center gap-2 rounded-md border border-border/60 bg-background px-2.5 py-1.5 text-sm',
              primaryMissing && 'border-destructive bg-destructive/5',
            )}
            title={primaryMissing ? t('missingRefTitle', { ref: primary }) : undefined}
          >
            <span className={cn('flex-1 font-mono text-xs', primaryMissing && 'text-destructive')}>
              {primary}
            </span>
            {primaryMissing && (
              <span className="text-[10px] uppercase tracking-wide text-destructive">
                {t('missingRefBadge')}
              </span>
            )}
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('chainRemove')}
              onClick={() => onPrimaryChange(null)}
            >
              <Trash2 className="size-3" />
            </Button>
          </div>
        ) : (
          <RefPicker
            availableModels={availableModels}
            exclude={fallbacks}
            ariaLabel={t('primary')}
            onAdd={(ref) => onPrimaryChange(ref)}
          />
        )}
      </div>

      <div className="space-y-2">
        <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
          {t('fallbacks')}
        </Label>
        <ChainEditor
          chain={fallbacks}
          availableModels={availableModels}
          exclude={primary ? [primary] : []}
          missingRefs={missingRefs}
          onMove={moveFallback}
          onRemove={removeFallback}
          onAdd={addFallback}
        />
      </div>
    </div>
  )
}

interface RefPickerProps {
  availableModels: string[]
  exclude: string[]
  ariaLabel: string
  onAdd: (ref: string) => void
}

// Autocomplete input that resolves a single `slug/model_id` ref. Shared by the
// primary picker and the fallbacks add-input inside ChainEditor.
function RefPicker({
  availableModels,
  exclude,
  ariaLabel,
  onAdd,
}: RefPickerProps): React.ReactElement {
  const t = useTranslations('adminPresets')
  const [draft, setDraft] = useState('')
  const [open, setOpen] = useState(false)

  const filtered = useMemo(() => {
    const q = draft.trim().toLowerCase()
    const pool = availableModels.filter((m) => !exclude.includes(m))
    if (!q) return pool.slice(0, 20)
    return pool.filter((m) => m.toLowerCase().includes(q)).slice(0, 20)
  }, [draft, availableModels, exclude])

  const handleAdd = (ref: string): void => {
    if (!ref.trim()) return
    onAdd(ref.trim())
    setDraft('')
    setOpen(false)
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-2">
        <Input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            window.setTimeout(() => setOpen(false), 120)
          }}
          placeholder={t('addModelPlaceholder')}
          aria-label={ariaLabel}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              handleAdd(draft)
            }
          }}
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() => handleAdd(draft)}
          disabled={!draft.trim()}
        >
          <Plus className="size-3.5" />
          <span>{t('chainAdd')}</span>
        </Button>
      </div>
      {open && filtered.length > 0 && (
        <ul
          role="listbox"
          className="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-md border border-border bg-popover py-1 text-sm shadow-md"
        >
          {filtered.map((ref) => (
            <li
              key={ref}
              role="option"
              aria-selected={false}
              className="cursor-pointer px-2.5 py-1 font-mono text-xs hover:bg-accent hover:text-accent-foreground"
              onMouseDown={(e) => {
                e.preventDefault()
                handleAdd(ref)
              }}
            >
              {ref}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface ChainEditorProps {
  chain: string[]
  availableModels: string[]
  exclude: string[]
  missingRefs: Set<string>
  onMove: (from: number, to: number) => void
  onRemove: (idx: number) => void
  onAdd: (ref: string) => void
}

function ChainEditor({
  chain,
  availableModels,
  exclude,
  missingRefs,
  onMove,
  onRemove,
  onAdd,
}: ChainEditorProps): React.ReactElement {
  const t = useTranslations('adminPresets')

  return (
    <div className="space-y-2">
      {chain.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t('chainEmpty')}</p>
      ) : (
        <ol className="space-y-1">
          {chain.map((ref, i) => {
            const missing = missingRefs.has(ref)
            return (
              <li
                key={`${ref}-${i}`}
                className={cn(
                  'flex items-center gap-2 rounded-md border border-border/60 bg-background px-2.5 py-1.5 text-sm',
                  missing && 'border-destructive bg-destructive/5',
                )}
                title={missing ? t('missingRefTitle', { ref }) : undefined}
                data-testid={`chain-entry-${i}`}
              >
                <span className="text-xs text-muted-foreground">{i + 1}.</span>
                <span className={cn('flex-1 font-mono text-xs', missing && 'text-destructive')}>
                  {ref}
                </span>
                {missing && (
                  <span className="text-[10px] uppercase tracking-wide text-destructive">
                    {t('missingRefBadge')}
                  </span>
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('chainMoveUp')}
                  onClick={() => onMove(i, i - 1)}
                  disabled={i === 0}
                >
                  <ArrowUp className="size-3" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('chainMoveDown')}
                  onClick={() => onMove(i, i + 1)}
                  disabled={i === chain.length - 1}
                >
                  <ArrowDown className="size-3" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('chainRemove')}
                  onClick={() => onRemove(i)}
                >
                  <Trash2 className="size-3" />
                </Button>
              </li>
            )
          })}
        </ol>
      )}

      <RefPicker
        availableModels={availableModels}
        exclude={[...chain, ...exclude]}
        ariaLabel={t('addModelAria')}
        onAdd={onAdd}
      />
    </div>
  )
}
