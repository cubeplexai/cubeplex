'use client'

import { useMemo, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Plus, Trash2 } from 'lucide-react'
import {
  createModel,
  type ApiClient,
  type ModelCreate,
  type ModelPresetEntry,
  type ModelUpdate,
  type VendorPreset,
} from '@cubeplex/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { cn } from '@/lib/utils'
import { ModelFormDialog } from '../ModelFormDialog'
import type { CreatedModel } from './wizardMachine'

interface ModelRow {
  key: string
  model_id: string
  display_name: string
  context_window: number
  max_tokens: number
  input_modalities: string[]
  reasoning: boolean
  cost_input: number
  cost_output: number
  cost_cache_read: number
  cost_cache_write: number
  checked: boolean
  custom: boolean
}

interface ModelsStepProps {
  client: ApiClient
  vendor: VendorPreset
  /** The endpoint chosen in step 2 — its preset_key. */
  presetKey: string
  providerId: string
  /** Models persisted on a prior visit to this step (the wizard keeps them when
   *  the user steps back). Seeds the dedupe cache so re-entering doesn't re-POST
   *  and 409 on the already-created ids. */
  existingModels: CreatedModel[]
  onModelsCreated: (models: CreatedModel[]) => void
}

export function ModelsStep({
  client,
  vendor,
  presetKey,
  providerId,
  existingModels,
  onModelsCreated,
}: ModelsStepProps) {
  const t = useTranslations('adminModels.wizard.models')
  const tw = useTranslations('adminModels.wizard')

  // Models the chosen endpoint serves (its model_ids mapped against the pool).
  const endpointModels = useMemo<ModelPresetEntry[]>(() => {
    const ep = vendor.endpoints.find((e) => e.preset_key === presetKey) ?? vendor.endpoints[0]
    const ids = new Set(ep?.model_ids ?? [])
    return vendor.models.filter((m) => ids.has(m.model_id))
  }, [vendor, presetKey])

  const [rows, setRows] = useState<ModelRow[]>(() =>
    endpointModels.map((m, i) => ({
      key: `preset-${i}`,
      model_id: m.model_id,
      display_name: m.display_name,
      context_window: m.context_window,
      max_tokens: m.max_tokens,
      input_modalities: m.input_modalities,
      reasoning: m.reasoning,
      cost_input: m.pricing.input,
      cost_output: m.pricing.output,
      cost_cache_read: m.pricing.cache_read ?? 0,
      cost_cache_write: m.pricing.cache_write ?? 0,
      checked: true,
      custom: false,
    })),
  )
  const [addOpen, setAddOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Models already created — by a prior (failed) attempt within this mount, or by
  // an earlier visit before the user stepped back (seeded from existingModels) —
  // keyed by model_id, so a retry/re-entry skips them instead of re-POSTing and
  // 409-ing on the duplicate id.
  const createdByModelId = useRef<Map<string, CreatedModel>>(
    new Map(existingModels.map((m) => [m.model_id, m])),
  )

  const checkedCount = rows.filter((r) => r.checked).length

  function toggle(key: string) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, checked: !r.checked } : r)))
  }

  // Custom models use the same full form as the models-management page (model id,
  // modalities, reasoning, context/limits, pricing) instead of a stripped id+name
  // pair — the dialog collects a ModelCreate, which we stage as a row (the POST
  // happens on Next, like every other selected row).
  function handleAddCustom(body: ModelCreate | ModelUpdate): Promise<void> {
    const m = body as ModelCreate
    setRows((prev) => [
      ...prev,
      {
        key: `custom-${Date.now()}`,
        model_id: m.model_id,
        display_name: m.display_name || m.model_id,
        context_window: m.context_window ?? 0,
        max_tokens: m.max_tokens ?? 0,
        input_modalities: m.input_modalities ?? ['text'],
        reasoning: m.reasoning ?? false,
        cost_input: m.cost_input ?? 0,
        cost_output: m.cost_output ?? 0,
        cost_cache_read: m.cost_cache_read ?? 0,
        cost_cache_write: m.cost_cache_write ?? 0,
        checked: true,
        custom: true,
      },
    ])
    setAddOpen(false)
    return Promise.resolve()
  }

  function removeRow(key: string) {
    setRows((prev) => prev.filter((r) => r.key !== key))
  }

  async function handleNext() {
    // Dedupe selected rows by vendor model_id (a custom row could collide with a
    // preset row); keep the first occurrence.
    const seen = new Set<string>()
    const selected = rows.filter((r) => r.checked && !seen.has(r.model_id) && seen.add(r.model_id))
    if (selected.length === 0) return
    setSaving(true)
    setError(null)
    try {
      const created: CreatedModel[] = []
      for (const r of selected) {
        const cached = createdByModelId.current.get(r.model_id)
        if (cached) {
          // Created on a prior attempt — don't re-POST (would 409 on the id).
          created.push(cached)
          continue
        }
        const body: ModelCreate = {
          model_id: r.model_id,
          display_name: r.display_name,
          context_window: r.context_window,
          max_tokens: r.max_tokens,
          input_modalities: r.input_modalities,
          reasoning: r.reasoning,
          cost_input: r.cost_input,
          cost_output: r.cost_output,
          cost_cache_read: r.cost_cache_read,
          cost_cache_write: r.cost_cache_write,
          enabled: false,
        }
        const model = await createModel(client, providerId, body)
        const entry: CreatedModel = {
          id: model.id,
          model_id: r.model_id,
          display_name: r.display_name,
        }
        createdByModelId.current.set(r.model_id, entry)
        created.push(entry)
      }
      onModelsCreated(created)
    } catch (e) {
      setError((e as Error).message || t('importFailed'))
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-4">
      <div>
        <h3 className="text-sm font-semibold">{t('heading')}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
      </div>

      <div className="flex flex-col gap-1.5">
        {rows.map((r) => (
          <label
            key={r.key}
            className={cn(
              'flex items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors',
              r.checked ? 'border-primary/40 bg-primary/5' : 'border-border/70 hover:border-border',
            )}
          >
            <Checkbox checked={r.checked} onCheckedChange={() => toggle(r.key)} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{r.display_name}</p>
              <p className="truncate text-xs text-muted-foreground">{r.model_id}</p>
            </div>
            {r.reasoning && (
              <Badge variant="secondary" className="font-normal">
                {t('reasoning')}
              </Badge>
            )}
            {r.custom && (
              <button
                type="button"
                aria-label={t('remove')}
                onClick={(e) => {
                  e.preventDefault()
                  removeRow(r.key)
                }}
                className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Trash2 className="size-3.5" />
              </button>
            )}
          </label>
        ))}
      </div>

      <Button
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => setAddOpen(true)}
      >
        <Plus className="size-3.5" />
        {t('addCustom')}
      </Button>

      <ModelFormDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        model={null}
        onSave={handleAddCustom}
      />

      {checkedCount === 0 && <p className="text-xs text-muted-foreground">{t('empty')}</p>}

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={checkedCount === 0 || saving}
          onClick={() => void handleNext()}
        >
          {saving ? t('importing') : tw('next')}
        </Button>
      </div>
    </div>
  )
}
