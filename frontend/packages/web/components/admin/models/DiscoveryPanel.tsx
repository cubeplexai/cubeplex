'use client'

import { useEffect, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  createProvider,
  discoverModels,
  discoverSavedModels,
  importDiscoveredModels,
  type ApiClient,
  type DiscoveredModel,
  type DiscoveryResponse,
  type ImportModelOutcome,
  type ImportModelSelection,
  type ModelField,
  type Provider,
  type ProviderCreate,
} from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface Props {
  client: ApiClient
  provider?: Provider
  draft?: ProviderCreate
  onProviderSaved?: (id: string) => void
  onImported: (providerId: string, models: ImportModelOutcome[]) => void
}
const required: ModelField[] = [
  'purpose',
  'context_window',
  'max_tokens',
  'input_modalities',
  'reasoning',
  'reasoning_levels',
  'cost_input',
  'cost_output',
  'cost_cache_read',
  'cost_cache_write',
]
const canSelect = (m: DiscoveredModel) =>
  !m.existing_model_db_id && !['embedding', 'image', 'audio'].includes(m.purpose)

export function DiscoveryPanel({ client, provider, draft, onProviderSaved, onImported }: Props) {
  const t = useTranslations('adminModels.discovery')
  const [preview, setPreview] = useState<DiscoveryResponse | null>(null)
  const [selections, setSelections] = useState<Record<string, ImportModelSelection>>({})
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [managementKey, setManagementKey] = useState('')
  const [context, setContext] = useState('')
  const [output, setOutput] = useState('')
  const savedId = useRef(provider?.id ?? null)
  const sequence = useRef(0)
  const discoveryController = useRef<AbortController | null>(null)
  useEffect(
    () => () => {
      sequence.current += 1
      discoveryController.current?.abort()
    },
    [],
  )

  async function discover(enrich = false) {
    discoveryController.current?.abort()
    const attempt = new AbortController()
    discoveryController.current = attempt
    const seq = ++sequence.current
    setBusy(true)
    setError(null)
    try {
      const ids = enrich ? [...selected] : []
      let response: DiscoveryResponse | null = null
      const batches = ids.length
        ? Array.from({ length: Math.ceil(ids.length / 100) }, (_, i) =>
            ids.slice(i * 100, i * 100 + 100),
          )
        : [[]]
      for (const batch of batches) {
        const body = { management_key: managementKey || undefined, selected_model_ids: batch }
        const next =
          provider || savedId.current
            ? await discoverSavedModels(client, savedId.current!, body, attempt.signal)
            : await discoverModels(
                client,
                {
                  api: (draft?.provider_type ?? 'openai-completions') as 'openai-completions',
                  base_url: draft!.base_url,
                  api_key: draft?.api_key,
                  auth_type: draft?.auth_type,
                  preset_slug: draft?.preset_slug,
                  discovery: draft?.discovery,
                  capability: draft?.capability,
                  extra_body: draft?.extra_body,
                  extra_headers: draft?.extra_headers,
                  ...body,
                },
                attempt.signal,
              )
        // Each preview is self-contained. Enrich the selected batch again just
        // before import so its short-lived token contains that batch's evidence.
        response = next
        if (seq !== sequence.current) return
      }
      if (seq !== sequence.current || !response) return
      setPreview(response)
      if (!enrich) {
        setSelections({})
        setSelected(
          new Set(
            response.candidates.filter((m) => canSelect(m) && m.eligible).map((m) => m.model_id),
          ),
        )
      }
    } catch (e) {
      if (seq === sequence.current) setError((e as Error).message)
    } finally {
      if (seq === sequence.current) {
        setBusy(false)
        setManagementKey('')
      }
    }
  }

  function patch(id: string, update: Partial<ImportModelSelection>) {
    setSelections((prev) => ({ ...prev, [id]: { ...prev[id], model_id: id, ...update } }))
  }
  function override(id: string, field: ModelField, value: unknown) {
    patch(id, { overrides: { ...selections[id]?.overrides, [field]: value } })
  }
  function groupApply() {
    setSelections((prev) => {
      const next = { ...prev }
      for (const id of selected) {
        next[id] = {
          ...prev[id],
          model_id: id,
          overrides: {
            ...prev[id]?.overrides,
            purpose: 'chat',
            input_modalities: ['text'],
            ...(context ? { context_window: Number(context) } : {}),
            ...(output ? { max_tokens: Number(output) } : {}),
          },
        }
      }
      return next
    })
  }
  function confirmSuggestions() {
    setSelections((prev) => {
      const next = { ...prev }
      for (const model of preview?.candidates ?? []) {
        if (!selected.has(model.model_id)) continue
        next[model.model_id] = {
          ...prev[model.model_id],
          model_id: model.model_id,
          confirm_fields: Object.entries(model.fields)
            .filter(([, evidence]) => evidence?.state === 'suggested')
            .map(([field]) => field as ModelField),
        }
      }
      return next
    })
  }
  async function importSelected() {
    if (!preview?.discovery_id || !selected.size) return
    setBusy(true)
    setError(null)
    const seq = ++sequence.current
    try {
      if (!savedId.current) {
        const created = await createProvider(client, {
          ...draft!,
          discovery_id: preview.discovery_id,
        })
        savedId.current = created.id
        onProviderSaved?.(created.id)
      }
      const outcomes: ImportModelOutcome[] = []
      const ids = [...selected]
      for (let i = 0; i < ids.length; i += 100) {
        const batch = ids.slice(i, i + 100)
        if (seq !== sequence.current) return
        const result = await importDiscoveredModels(
          client,
          savedId.current!,
          preview.discovery_id,
          batch.map((id) => selections[id] ?? { model_id: id }),
        )
        outcomes.push(...result.results)
      }
      if (seq === sequence.current) onImported(savedId.current!, outcomes)
    } catch (e) {
      if (seq === sequence.current) setError((e as Error).message)
    } finally {
      if (seq === sequence.current) {
        setBusy(false)
        setManagementKey('')
      }
    }
  }

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{t('title')}</h3>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void discover()}>
          {busy ? t('working') : t('discover')}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t('hint')}</p>
      {(provider?.preset_slug?.startsWith('cliproxyapi') ||
        draft?.preset_slug?.startsWith('cliproxyapi')) && (
        <div className="flex flex-col gap-1">
          <Label htmlFor="discovery-management-key">{t('managementKey')}</Label>
          <Input
            id="discovery-management-key"
            type="password"
            autoComplete="new-password"
            value={managementKey}
            onChange={(e) => setManagementKey(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">{t('managementHint')}</p>
        </div>
      )}
      {preview && (
        <>
          <p className="text-xs text-muted-foreground">
            {preview.status === 'not_supported'
              ? t('staticHint')
              : t('count', { count: preview.candidates.length })}
          </p>
          {preview.diagnostics.map((d) => (
            <p key={d.code} role="status" className="text-xs text-warning-fg">
              {d.message}
            </p>
          ))}
          {preview.status === 'complete' && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    setSelected(
                      new Set(preview.candidates.filter(canSelect).map((m) => m.model_id)),
                    )
                  }
                >
                  {t('selectAll')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setSelected(new Set())}
                >
                  {t('clear')}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !selected.size || selected.size > 100}
                  onClick={() => void discover(true)}
                >
                  {t('enrich')}
                </Button>
              </div>
              <div className="flex flex-col gap-2 border-y border-border py-3">
                <p className="text-xs">{t('groupHint')}</p>
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    className="w-40"
                    type="number"
                    min="1"
                    step="1"
                    value={context}
                    aria-label={t('context_window')}
                    placeholder={t('context_window')}
                    onChange={(e) => setContext(e.target.value)}
                  />
                  <Input
                    className="w-40"
                    type="number"
                    min="1"
                    step="1"
                    value={output}
                    aria-label={t('max_tokens')}
                    placeholder={t('max_tokens')}
                    onChange={(e) => setOutput(e.target.value)}
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy || !selected.size}
                    onClick={groupApply}
                  >
                    {t('applyGroup')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy || !selected.size}
                    onClick={confirmSuggestions}
                  >
                    {t('confirmSuggestions')}
                  </Button>
                </div>
              </div>
              <div className="max-h-96 overflow-y-auto divide-y divide-border">
                {preview.candidates.map((model) => (
                  <div key={model.model_id} className="py-3">
                    <label className="flex items-center gap-2 text-sm">
                      <Checkbox
                        disabled={busy || !canSelect(model)}
                        checked={selected.has(model.model_id)}
                        onCheckedChange={(checked) =>
                          setSelected((prev) => {
                            const next = new Set(prev)
                            if (checked) next.add(model.model_id)
                            else next.delete(model.model_id)
                            return next
                          })
                        }
                      />
                      <span className="break-all font-mono">{model.model_id}</span>
                      <span className="text-xs text-muted-foreground">
                        {model.existing_model_db_id
                          ? t('existing')
                          : t(model.eligible ? 'complete' : 'needsReview')}
                      </span>
                    </label>
                    <details
                      className="mt-1 pl-6 text-xs"
                      onToggle={(event) => {
                        const open = event.currentTarget.open
                        setExpanded((prev) => {
                          const next = new Set(prev)
                          if (open) next.add(model.model_id)
                          else next.delete(model.model_id)
                          return next
                        })
                      }}
                    >
                      <summary className="cursor-pointer text-muted-foreground">
                        {t('fields')}
                      </summary>
                      <div className="mt-2 flex flex-col gap-2">
                        {expanded.has(model.model_id) && (
                          <>
                            {[
                              ...new Set([
                                ...required,
                                ...(Object.keys(model.fields) as ModelField[]),
                              ]),
                            ].map((field) => {
                              const evidence = model.fields[field]
                              const selection = selections[model.model_id]
                              return (
                                <div key={field} className="flex flex-col gap-1">
                                  <span>
                                    {t(field)}:{' '}
                                    {evidence ? JSON.stringify(evidence.value) : t('unknown')}
                                    {' · '}
                                    {evidence?.source ?? ''} ·{' '}
                                    {evidence ? t(evidence.state) : t('unknown')}
                                  </span>
                                  {evidence?.state === 'conflict' && (
                                    <select
                                      className="rounded border border-input p-1"
                                      aria-label={t('resolve', { field: t(field) })}
                                      value={selection?.resolutions?.[field] ?? ''}
                                      onChange={(e) =>
                                        patch(model.model_id, {
                                          resolutions: {
                                            ...selection?.resolutions,
                                            [field]: e.target.value,
                                          },
                                        })
                                      }
                                    >
                                      <option value="">{t('chooseSource')}</option>
                                      {evidence.alternatives.map((d, i) => (
                                        <option key={i} value={d.source}>
                                          {d.source}: {JSON.stringify(d.value)}
                                        </option>
                                      ))}
                                    </select>
                                  )}
                                  {evidence?.state === 'suggested' && (
                                    <label className="flex items-center gap-2">
                                      <Checkbox
                                        checked={
                                          selection?.confirm_fields?.includes(field) ?? false
                                        }
                                        onCheckedChange={(checked) =>
                                          patch(model.model_id, {
                                            confirm_fields: checked
                                              ? [...(selection?.confirm_fields ?? []), field]
                                              : selection?.confirm_fields?.filter(
                                                  (f) => f !== field,
                                                ),
                                          })
                                        }
                                      />
                                      {t('confirmField')}
                                    </label>
                                  )}
                                  {[
                                    'context_window',
                                    'max_tokens',
                                    'cost_input',
                                    'cost_output',
                                    'cost_cache_read',
                                    'cost_cache_write',
                                  ].includes(field) && (
                                    <Input
                                      type="number"
                                      min={field.startsWith('cost') ? '0' : '1'}
                                      step={field.startsWith('cost') ? 'any' : '1'}
                                      aria-label={`${model.model_id} ${t(field)}`}
                                      value={String(
                                        selection?.overrides?.[
                                          field as keyof NonNullable<
                                            ImportModelSelection['overrides']
                                          >
                                        ] ?? '',
                                      )}
                                      placeholder={t('override')}
                                      onChange={(e) => {
                                        const overrides = { ...selection?.overrides }
                                        if (e.target.value)
                                          Object.assign(overrides, {
                                            [field]: Number(e.target.value),
                                          })
                                        else delete overrides[field as keyof typeof overrides]
                                        patch(model.model_id, { overrides })
                                      }}
                                    />
                                  )}
                                  {field === 'purpose' && (
                                    <label className="flex items-center gap-2">
                                      <Checkbox
                                        checked={selection?.overrides?.purpose === 'chat'}
                                        onCheckedChange={(checked) =>
                                          override(
                                            model.model_id,
                                            field,
                                            checked ? 'chat' : undefined,
                                          )
                                        }
                                      />
                                      {t('confirmChat')}
                                    </label>
                                  )}
                                  {field === 'input_modalities' && (
                                    <label className="flex items-center gap-2">
                                      <Checkbox
                                        checked={
                                          selection?.overrides?.input_modalities?.includes(
                                            'image',
                                          ) ?? false
                                        }
                                        onCheckedChange={(checked) =>
                                          override(
                                            model.model_id,
                                            field,
                                            checked ? ['text', 'image'] : ['text'],
                                          )
                                        }
                                      />
                                      {t('declareImage')}
                                    </label>
                                  )}
                                </div>
                              )
                            })}
                          </>
                        )}
                      </div>
                    </details>
                  </div>
                ))}
              </div>
              <Button disabled={busy || !selected.size} onClick={() => void importSelected()}>
                {busy ? t('working') : t('import', { count: selected.size })}
              </Button>
            </>
          )}
        </>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger-fg">
          {error}
        </p>
      )}
    </section>
  )
}
