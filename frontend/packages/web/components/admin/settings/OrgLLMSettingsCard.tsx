'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Check, Plus, X } from 'lucide-react'
import { createApiClient, fetchOrgLLMSettings, updateOrgLLMSettings } from '@cubebox/core'
import type { OrgLLMSettings } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxGroup,
  ComboboxInput,
  ComboboxItem,
  ComboboxLabel,
  ComboboxList,
} from '@/components/ui/combobox'
import { ReadinessBadge } from '@/components/admin/models/ReadinessBadge'
import { useAllModels, type ProviderModelOption } from '@/hooks/useAllModels'
import { cn } from '@/lib/utils'

// A model is selectable when it is enabled and not in a hard-error/unavailable
// state. ready/degraded/stale stay usable; provider_error/auth_error/model_error/
// unavailable (or a disabled model) are shown but not selectable.
function isUsable(opt: ProviderModelOption): boolean {
  if (!opt.enabled) return false
  return opt.readiness === 'ready' || opt.readiness === 'degraded' || opt.readiness === 'stale'
}

interface DraftState {
  defaultModel: string
  fallbackModels: string[]
}

function emptyDraft(): DraftState {
  return { defaultModel: '', fallbackModels: [] }
}

function fromSettings(s: OrgLLMSettings): DraftState {
  return {
    defaultModel: s.default_model ?? '',
    fallbackModels: s.fallback_models ?? [],
  }
}

function isDirty(a: DraftState, b: DraftState): boolean {
  if (a.defaultModel !== b.defaultModel) return true
  if (a.fallbackModels.length !== b.fallbackModels.length) return true
  return a.fallbackModels.some((m, i) => m !== b.fallbackModels[i])
}

function groupOptionsByProvider(opts: ProviderModelOption[]) {
  const groups = new Map<string, ProviderModelOption[]>()
  for (const o of opts) {
    const arr = groups.get(o.providerName) ?? []
    arr.push(o)
    groups.set(o.providerName, arr)
  }
  return Array.from(groups.entries()).map(([providerName, items]) => ({ providerName, items }))
}

export function OrgLLMSettingsCard() {
  const t = useTranslations('adminSettings')
  const client = useMemo(() => createApiClient(''), [])
  const { options: modelOptions, loading: modelsLoading, error: modelsError } = useAllModels()

  const [server, setServer] = useState<DraftState>(emptyDraft())
  const [draft, setDraft] = useState<DraftState>(emptyDraft())
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<Error | null>(null)

  const [showAddFallback, setShowAddFallback] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    fetchOrgLLMSettings(client)
      .then((s) => {
        if (cancelled) return
        const next = fromSettings(s)
        setServer(next)
        setDraft(next)
      })
      .catch((e: Error) => {
        if (!cancelled) setLoadError(e)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client])

  const dirty = isDirty(server, draft)

  // Hide saved indicator after a few seconds.
  useEffect(() => {
    if (!savedAt) return
    const id = setTimeout(() => setSavedAt(null), 2500)
    return () => clearTimeout(id)
  }, [savedAt])

  function setDefaultModel(value: string | null) {
    setDraft((d) => ({ ...d, defaultModel: value ?? '' }))
    setSavedAt(null)
    setSaveError(null)
  }

  function addFallback(value: string | null) {
    setShowAddFallback(false)
    if (!value) return
    setDraft((d) => {
      if (d.fallbackModels.includes(value)) return d
      return { ...d, fallbackModels: [...d.fallbackModels, value] }
    })
    setSavedAt(null)
    setSaveError(null)
  }

  function removeFallback(idx: number) {
    setDraft((d) => ({
      ...d,
      fallbackModels: d.fallbackModels.filter((_, i) => i !== idx),
    }))
    setSavedAt(null)
    setSaveError(null)
  }

  function discard() {
    setDraft(server)
    setShowAddFallback(false)
    setSaveError(null)
  }

  async function save() {
    if (!dirty || saving) return
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await updateOrgLLMSettings(client, {
        default_model: draft.defaultModel || null,
        fallback_models: draft.fallbackModels,
      })
      const next = fromSettings(updated)
      setServer(next)
      setDraft(next)
      setSavedAt(Date.now())
    } catch (e) {
      setSaveError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const grouped = useMemo(() => groupOptionsByProvider(modelOptions), [modelOptions])
  const availableForFallback = useMemo(
    () =>
      modelOptions.filter(
        (o) => o.ref !== draft.defaultModel && !draft.fallbackModels.includes(o.ref),
      ),
    [modelOptions, draft.defaultModel, draft.fallbackModels],
  )
  const availableGrouped = useMemo(
    () => groupOptionsByProvider(availableForFallback),
    [availableForFallback],
  )

  if (loading || modelsLoading) {
    return (
      <Card>
        <p className="text-xs text-muted-foreground">{t('loading')}</p>
      </Card>
    )
  }
  if (loadError || modelsError) {
    const msg = (loadError ?? modelsError)?.message ?? ''
    return (
      <Card>
        <p className="text-xs text-destructive">{t('loadFailed', { message: msg })}</p>
      </Card>
    )
  }

  const noModels = modelOptions.length === 0

  return (
    <Card>
      <header className="flex flex-col gap-0.5 border-b border-border/60 pb-4">
        <h3 className="text-sm font-semibold">{t('llmTitle')}</h3>
        <p className="text-xs text-muted-foreground">{t('llmSubtitle')}</p>
      </header>

      {noModels ? (
        <p className="rounded-md border border-dashed border-border/60 bg-muted/20 px-4 py-6 text-center text-xs text-muted-foreground">
          {t('noModels')}
        </p>
      ) : (
        <>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="default-model">{t('defaultModel')}</Label>
            <Combobox
              value={draft.defaultModel}
              onValueChange={(v) => setDefaultModel(v as string | null)}
            >
              <ComboboxInput id="default-model" placeholder={t('defaultModelPlaceholder')} />
              <ComboboxContent>
                <ComboboxEmpty>{t('noMatchingModels')}</ComboboxEmpty>
                <ComboboxList>
                  {grouped.map((g) => (
                    <ComboboxGroup key={g.providerName}>
                      <ComboboxLabel>{g.providerName}</ComboboxLabel>
                      {g.items.map((opt) => (
                        <ComboboxItem key={opt.ref} value={opt.ref} disabled={!isUsable(opt)}>
                          <ModelOptionLabel opt={opt} />
                        </ComboboxItem>
                      ))}
                    </ComboboxGroup>
                  ))}
                </ComboboxList>
              </ComboboxContent>
            </Combobox>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>{t('fallbackChain')}</Label>
            {draft.fallbackModels.length > 0 ? (
              <div className="flex flex-wrap items-center gap-1.5">
                {draft.fallbackModels.map((ref, idx) => (
                  <span
                    key={`${ref}-${idx}`}
                    className="inline-flex items-center gap-1 rounded-md border border-border/70 bg-muted/40 px-2 py-1 text-xs font-medium"
                  >
                    {ref}
                    <button
                      type="button"
                      onClick={() => removeFallback(idx)}
                      className="rounded p-0.5 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                      aria-label={t('fallbackRemove', { model: ref })}
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                ))}
                {!showAddFallback && (
                  <Button
                    variant="ghost"
                    size="xs"
                    className="h-6 gap-1 text-xs"
                    onClick={() => setShowAddFallback(true)}
                  >
                    <Plus className="size-3" />
                    {t('fallbackAdd')}
                  </Button>
                )}
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">{t('fallbackEmpty')}</span>
                {!showAddFallback && (
                  <Button
                    variant="ghost"
                    size="xs"
                    className="h-6 gap-1 text-xs"
                    onClick={() => setShowAddFallback(true)}
                  >
                    <Plus className="size-3" />
                    {t('fallbackAdd')}
                  </Button>
                )}
              </div>
            )}

            {showAddFallback && (
              <Combobox onValueChange={(v) => addFallback(v as string | null)}>
                <ComboboxInput placeholder={t('fallbackPlaceholder')} showClear />
                <ComboboxContent>
                  <ComboboxEmpty>{t('noMatchingModels')}</ComboboxEmpty>
                  <ComboboxList>
                    {availableGrouped.map((g) => (
                      <ComboboxGroup key={g.providerName}>
                        <ComboboxLabel>{g.providerName}</ComboboxLabel>
                        {g.items.map((opt) => (
                          <ComboboxItem key={opt.ref} value={opt.ref} disabled={!isUsable(opt)}>
                            <ModelOptionLabel opt={opt} />
                          </ComboboxItem>
                        ))}
                      </ComboboxGroup>
                    ))}
                  </ComboboxList>
                </ComboboxContent>
              </Combobox>
            )}
          </div>

          <div className="flex items-center justify-end gap-2 border-t border-border/60 pt-4">
            {saveError && (
              <span className="mr-auto text-xs text-destructive" data-testid="settings-save-error">
                {t('saveFailed', { message: saveError })}
              </span>
            )}
            {!saveError && savedAt && (
              <span
                className="mr-auto inline-flex items-center gap-1 text-xs text-success-fg"
                data-testid="settings-saved"
              >
                <Check className="size-3" />
                {t('saved')}
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={discard}
              disabled={!dirty || saving}
              data-testid="settings-discard"
            >
              {t('discard')}
            </Button>
            <Button
              size="sm"
              onClick={() => void save()}
              disabled={!dirty || saving}
              data-testid="settings-save"
            >
              {saving ? t('saving') : t('save')}
            </Button>
          </div>
        </>
      )}
    </Card>
  )
}

function ModelOptionLabel({ opt }: { opt: ProviderModelOption }) {
  const t = useTranslations('adminSettings')
  const usable = isUsable(opt)
  return (
    <span className="flex w-full items-center justify-between gap-2">
      <span className={cn('truncate', !usable && 'text-muted-foreground')}>{opt.modelId}</span>
      {!usable && (
        <span className="flex shrink-0 items-center gap-1.5">
          <ReadinessBadge readiness={opt.enabled ? opt.readiness : 'unavailable'} />
          <span className="text-[11px] text-muted-foreground">{t('modelUnavailable')}</span>
        </span>
      )}
    </span>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <section
      data-testid="org-llm-settings-card"
      className={cn(
        'flex flex-col gap-4 rounded-xl border border-border/70 bg-card/40 p-5 shadow-sm',
      )}
    >
      {children}
    </section>
  )
}
