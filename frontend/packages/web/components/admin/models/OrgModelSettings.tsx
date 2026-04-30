'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { X, Plus } from 'lucide-react'
import type {
  Provider,
  Model,
  OrgLLMSettings,
  OrgLLMSettingsUpdate,
  ApiClient,
} from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Combobox,
  ComboboxInput,
  ComboboxContent,
  ComboboxList,
  ComboboxItem,
  ComboboxGroup,
  ComboboxLabel,
  ComboboxEmpty,
} from '@/components/ui/combobox'

interface ComboboxOption {
  value: string
  label: string
}

interface OrgModelSettingsProps {
  providers: Provider[]
  models: Model[]
  settings: OrgLLMSettings | null
  client: ApiClient
  onUpdateSettings: (client: ApiClient, body: OrgLLMSettingsUpdate) => Promise<void>
}

function buildModelOptions(providers: Provider[], models: Model[]): ComboboxOption[] {
  const options: ComboboxOption[] = []
  for (const provider of providers) {
    const providerModels = provider.models ?? models.filter((m) => m.provider_id === provider.id)
    for (const model of providerModels) {
      options.push({
        value: `${provider.name}/${model.model_id}`,
        label: `${provider.name}/${model.model_id}`,
      })
    }
  }
  return options
}

export function OrgModelSettings({
  providers,
  models,
  settings,
  client,
  onUpdateSettings,
}: OrgModelSettingsProps) {
  const modelOptions = buildModelOptions(providers, models)
  const [defaultModel, setDefaultModel] = useState(settings?.default_model ?? '')
  const [fallbackModels, setFallbackModels] = useState<string[]>(settings?.fallback_models ?? [])
  const [showAddFallback, setShowAddFallback] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const initialized = useRef(false)

  // Sync from settings prop
  useEffect(() => {
    if (settings && !initialized.current) {
      setDefaultModel(settings.default_model ?? '')
      setFallbackModels(settings.fallback_models ?? [])
      initialized.current = true
    }
  }, [settings])

  // Reset initialized when settings changes from null to non-null
  useEffect(() => {
    if (settings) {
      initialized.current = true
    } else {
      initialized.current = false
    }
  }, [settings])

  const debouncedSave = useCallback(
    (body: OrgLLMSettingsUpdate) => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      debounceRef.current = setTimeout(() => {
        void onUpdateSettings(client, body)
      }, 500)
    },
    [client, onUpdateSettings],
  )

  function handleDefaultModelChange(value: string | null) {
    const newVal = value ?? ''
    setDefaultModel(newVal)
    debouncedSave({
      default_model: newVal || null,
      fallback_models: fallbackModels,
    })
  }

  function addFallback(value: string | null) {
    if (!value) return
    if (fallbackModels.includes(value)) {
      setShowAddFallback(false)
      return
    }
    const newChain = [...fallbackModels, value]
    setFallbackModels(newChain)
    setShowAddFallback(false)
    debouncedSave({
      default_model: defaultModel || null,
      fallback_models: newChain,
    })
  }

  function removeFallback(idx: number) {
    const newChain = fallbackModels.filter((_, i) => i !== idx)
    setFallbackModels(newChain)
    debouncedSave({
      default_model: defaultModel || null,
      fallback_models: newChain,
    })
  }

  // Filter out already-selected fallbacks from combobox options
  const availableForFallback = modelOptions.filter(
    (o) => !fallbackModels.includes(o.value) && o.value !== defaultModel,
  )

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h4 className="text-sm font-medium">组织默认模型</h4>
        <p className="mt-0.5 text-xs text-muted-foreground">
          设置组织级默认 LLM 模型，所有工作空间将使用此配置
        </p>
      </div>

      {/* Default Model */}
      <div className="flex flex-col gap-1.5">
        <Label>默认模型</Label>
        <Combobox
          value={defaultModel}
          onValueChange={(v) => handleDefaultModelChange(v as string | null)}
        >
          <ComboboxInput placeholder="选择默认模型..." />
          <ComboboxContent>
            <ComboboxEmpty>无匹配模型</ComboboxEmpty>
            <ComboboxList>
              {groupByProvider(modelOptions).map((group) => (
                <ComboboxGroup key={group.provider}>
                  <ComboboxLabel>{group.provider}</ComboboxLabel>
                  {group.options.map((opt) => (
                    <ComboboxItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </ComboboxItem>
                  ))}
                </ComboboxGroup>
              ))}
            </ComboboxList>
          </ComboboxContent>
        </Combobox>
      </div>

      {/* Fallback Chain */}
      <div className="flex flex-col gap-1.5">
        <Label>Fallback 链</Label>
        {fallbackModels.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1.5">
            {fallbackModels.map((model, idx) => (
              <span
                key={`${model}-${idx}`}
                className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-1 text-xs font-medium"
              >
                {model}
                <button
                  type="button"
                  onClick={() => removeFallback(idx)}
                  className="rounded p-0.5 text-muted-foreground hover:text-foreground hover:bg-muted/80"
                  aria-label={`Remove ${model}`}
                >
                  <X className="size-3" />
                </button>
              </span>
            ))}
            {!showAddFallback && (
              <Button
                variant="ghost"
                size="xs"
                onClick={() => setShowAddFallback(true)}
                className="h-6 gap-1 text-xs"
              >
                <Plus className="size-3" />
                添加
              </Button>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">暂无 fallback 模型</span>
            {!showAddFallback && (
              <Button
                variant="ghost"
                size="xs"
                onClick={() => setShowAddFallback(true)}
                className="h-6 gap-1 text-xs"
              >
                <Plus className="size-3" />
                添加
              </Button>
            )}
          </div>
        )}

        {showAddFallback && (
          <Combobox onValueChange={(v) => addFallback(v as string | null)}>
            <ComboboxInput placeholder="选择 fallback 模型..." showClear />
            <ComboboxContent>
              <ComboboxEmpty>无可用模型</ComboboxEmpty>
              <ComboboxList>
                {groupByProvider(availableForFallback).map((group) => (
                  <ComboboxGroup key={group.provider}>
                    <ComboboxLabel>{group.provider}</ComboboxLabel>
                    {group.options.map((opt) => (
                      <ComboboxItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </ComboboxItem>
                    ))}
                  </ComboboxGroup>
                ))}
              </ComboboxList>
            </ComboboxContent>
          </Combobox>
        )}
      </div>
    </div>
  )
}

function groupByProvider(options: ComboboxOption[]) {
  const groups: { provider: string; options: ComboboxOption[] }[] = []
  for (const opt of options) {
    const provider = opt.value.split('/')[0]
    let group = groups.find((g) => g.provider === provider)
    if (!group) {
      group = { provider, options: [] }
      groups.push(group)
    }
    group.options.push(opt)
  }
  return groups
}
