'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Brain, Cable, Check, Pencil, Trash2, X } from 'lucide-react'
import type { ApiClient, Model, TestResult } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface ModelRowProps {
  model: Model
  client: ApiClient
  onEdit: (model: Model) => void
  onDelete: (model: Model) => void
  onTest: (client: ApiClient, providerId: string, body: { model_id: string }) => Promise<TestResult>
}

function formatCost(cost: number): string {
  if (cost === 0) return '0'
  if (cost < 0.01) return cost.toFixed(6)
  return cost.toFixed(4)
}

export function ModelRow({ model, client, onEdit, onDelete, onTest }: ModelRowProps) {
  const t = useTranslations('adminModels')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestResult | null>(null)

  async function handleTest() {
    if (testing) return
    setTesting(true)
    setTestResult(null)
    try {
      const result = await onTest(client, model.provider_id, { model_id: model.model_id })
      setTestResult(result)
    } catch (e) {
      setTestResult({ ok: false, error: (e as Error).message, latency_ms: 0 })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div
      data-testid={`model-row-${model.model_id}`}
      className={cn(
        'flex items-center gap-3 rounded-lg border px-3 py-2.5 text-xs transition-colors',
        model.is_system
          ? 'border-border/40 bg-muted/20'
          : 'border-border/70 bg-card/40 hover:bg-accent/30',
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-mono font-medium text-foreground">{model.model_id}</span>
          {model.reasoning && (
            <Brain className="size-3.5 shrink-0 text-purple-500" aria-label="reasoning" />
          )}
          {model.is_system && (
            <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
              {t('systemBadge')}
            </Badge>
          )}
        </div>
        {model.display_name && model.display_name !== model.model_id && (
          <span className="mt-0.5 block truncate text-muted-foreground">{model.display_name}</span>
        )}
      </div>

      <div className="hidden shrink-0 items-center gap-1 sm:flex">
        {model.input_modalities.map((mod) => (
          <Badge key={mod} variant="outline" className="px-1.5 text-[10px]">
            {mod}
          </Badge>
        ))}
      </div>

      <span
        className="hidden min-w-[60px] shrink-0 text-right text-muted-foreground md:block"
        title={`context window: ${model.context_window} tokens`}
      >
        {model.context_window > 0 ? `${(model.context_window / 1000).toFixed(0)}K` : '-'}
      </span>

      {/* Costs stored as $ per 1M tokens (input/output) */}
      <span
        className="hidden min-w-[100px] shrink-0 text-right text-muted-foreground lg:block"
        title={t('costPerMillion')}
      >
        {model.cost_input > 0 || model.cost_output > 0
          ? `$${formatCost(model.cost_input)}/$${formatCost(model.cost_output)}`
          : '-'}
      </span>

      {testResult && (
        <span
          data-testid={`model-test-result-${model.model_id}`}
          className={cn(
            'shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium',
            testResult.ok
              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
              : 'bg-destructive/10 text-destructive',
          )}
          title={testResult.error ?? ''}
        >
          {testResult.ok ? t('testOk', { latency: testResult.latency_ms }) : t('testFailed')}
        </span>
      )}

      <div className="flex shrink-0 items-center gap-1">
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => void handleTest()}
          disabled={testing}
          aria-label={t('test')}
          title={testing ? t('testing') : t('test')}
        >
          <Cable className={cn('size-3', testing && 'animate-pulse')} />
        </Button>

        {!model.is_system && !confirmOpen && (
          <>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => onEdit(model)}
              aria-label={`Edit ${model.model_id}`}
            >
              <Pencil className="size-3" />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => setConfirmOpen(true)}
              aria-label={`Delete ${model.model_id}`}
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="size-3" />
            </Button>
          </>
        )}

        {!model.is_system && confirmOpen && (
          <div className="flex items-center gap-1 rounded-md border border-destructive/30 bg-destructive/5 px-1.5 py-0.5">
            <span className="text-[11px] text-destructive">
              {t('deleteModelConfirm', { name: model.model_id })}
            </span>
            <button
              type="button"
              className="rounded p-0.5 text-destructive hover:bg-destructive/20"
              onClick={() => {
                setConfirmOpen(false)
                onDelete(model)
              }}
              aria-label="confirm delete"
              data-testid={`model-row-${model.model_id}-confirm-delete`}
            >
              <Check className="size-3" />
            </button>
            <button
              type="button"
              className="rounded p-0.5 text-muted-foreground hover:bg-muted"
              onClick={() => setConfirmOpen(false)}
              aria-label="cancel delete"
            >
              <X className="size-3" />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
