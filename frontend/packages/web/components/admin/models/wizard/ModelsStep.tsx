'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Plus, Trash2 } from 'lucide-react'
import { createModel, type ApiClient, type ModelCreate, type ProviderPreset } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

interface ModelRow {
  key: string
  model_id: string
  display_name: string
  context_window: number
  max_tokens: number
  input_modalities: string[]
  reasoning: boolean
  checked: boolean
  custom: boolean
}

interface ModelsStepProps {
  client: ApiClient
  preset: ProviderPreset
  providerId: string
  onModelsCreated: (modelDbIds: string[]) => void
}

export function ModelsStep({ client, preset, providerId, onModelsCreated }: ModelsStepProps) {
  const t = useTranslations('adminModels.wizard.models')
  const tw = useTranslations('adminModels.wizard')

  const [rows, setRows] = useState<ModelRow[]>(() =>
    preset.default_models.map((m, i) => ({
      key: `preset-${i}`,
      model_id: m.model_id,
      display_name: m.display_name,
      context_window: m.context_window,
      max_tokens: m.max_tokens,
      input_modalities: m.input_modalities,
      reasoning: m.reasoning,
      checked: true,
      custom: false,
    })),
  )
  const [draftId, setDraftId] = useState('')
  const [draftName, setDraftName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const checkedCount = rows.filter((r) => r.checked).length

  function toggle(key: string) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, checked: !r.checked } : r)))
  }

  function addCustom() {
    const id = draftId.trim()
    if (!id) return
    setRows((prev) => [
      ...prev,
      {
        key: `custom-${Date.now()}`,
        model_id: id,
        display_name: draftName.trim() || id,
        context_window: 128000,
        max_tokens: 4096,
        input_modalities: ['text'],
        reasoning: false,
        checked: true,
        custom: true,
      },
    ])
    setDraftId('')
    setDraftName('')
  }

  function removeRow(key: string) {
    setRows((prev) => prev.filter((r) => r.key !== key))
  }

  async function handleNext() {
    const selected = rows.filter((r) => r.checked)
    if (selected.length === 0) return
    setSaving(true)
    setError(null)
    try {
      const ids: string[] = []
      for (const r of selected) {
        const body: ModelCreate = {
          model_id: r.model_id,
          display_name: r.display_name,
          context_window: r.context_window,
          max_tokens: r.max_tokens,
          input_modalities: r.input_modalities,
          reasoning: r.reasoning,
          enabled: false,
        }
        const model = await createModel(client, providerId, body)
        ids.push(model.id)
      }
      onModelsCreated(ids)
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

      <div className="flex items-end gap-2 rounded-lg border border-dashed border-border/70 p-3">
        <div className="flex flex-1 flex-col gap-1">
          <Input
            value={draftId}
            onChange={(e) => setDraftId(e.target.value)}
            placeholder={t('modelId')}
            aria-label={t('modelId')}
          />
        </div>
        <div className="flex flex-1 flex-col gap-1">
          <Input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            placeholder={t('displayName')}
            aria-label={t('displayName')}
          />
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!draftId.trim()}
          onClick={addCustom}
        >
          <Plus className="size-3.5" />
          {t('add')}
        </Button>
      </div>

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
