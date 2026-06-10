'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Brain, Check, Loader2, Pencil, RotateCw, TriangleAlert, Trash2, X } from 'lucide-react'
import { testModel, type ApiClient, type Model } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { formatProbeDetail } from '@/lib/probeDetail'
import { ReadinessBadge } from './ReadinessBadge'

interface ModelRowProps {
  model: Model
  client: ApiClient
  providerId: string
  onEdit: (model: Model) => void
  onDelete: (model: Model) => void
  onRetested?: () => void
}

function formatCost(cost: number): string {
  if (cost === 0) return '0'
  if (cost < 0.01) return cost.toFixed(6)
  return cost.toFixed(4)
}

interface TestIssue {
  name: string
  status: string
  detail: string
}

// Pull the warn/fail probe steps (with their reason) out of last_test_summary so
// a degraded/error model can explain itself instead of just showing a dot.
function testIssues(summary: Record<string, unknown> | undefined): TestIssue[] {
  const steps = (summary?.steps as Array<Record<string, unknown>> | undefined) ?? []
  return steps
    .filter((s) => s.status === 'warn' || s.status === 'fail')
    .map((s) => {
      const error = s.error as Record<string, unknown> | null | undefined
      return {
        name: String(s.name ?? ''),
        status: String(s.status ?? ''),
        detail: formatProbeDetail(String(s.detail ?? error?.message ?? '')),
      }
    })
}

export function ModelRow({
  model,
  client,
  providerId,
  onEdit,
  onDelete,
  onRetested,
}: ModelRowProps) {
  const tExtra = useTranslations('adminModelsExtra')
  const t = useTranslations('adminModels')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [retesting, setRetesting] = useState(false)
  const issues = testIssues(model.last_test_summary)

  async function handleRetest() {
    setRetesting(true)
    try {
      await testModel(client, providerId, model.id)
      onRetested?.()
    } finally {
      setRetesting(false)
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
            <Brain className="size-3.5 shrink-0 text-info-fg" aria-label="reasoning" />
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
        {issues.length > 0 && (
          <ul className="mt-1 flex flex-col gap-0.5">
            {issues.map((i) => {
              const isFail = i.status === 'fail'
              return (
                <li key={i.name} className="flex items-start gap-1.5 text-[11px]">
                  {isFail ? (
                    <X className="mt-px size-3 shrink-0 text-destructive" />
                  ) : (
                    <TriangleAlert className="mt-px size-3 shrink-0 text-warning-fg" />
                  )}
                  <span className={cn('min-w-0', isFail ? 'text-destructive' : 'text-warning-fg')}>
                    <span className="font-medium">{i.name}</span>
                    {i.detail ? ` — ${i.detail}` : ''}
                  </span>
                </li>
              )
            })}
          </ul>
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

      <div className="hidden shrink-0 items-center md:flex">
        <ReadinessBadge readiness={model.readiness ?? 'ready'} />
      </div>

      <div className="flex shrink-0 items-center gap-1">
        {!confirmOpen && (
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => void handleRetest()}
            disabled={retesting}
            aria-label={t('retest', { name: model.model_id })}
            data-testid={`model-row-${model.model_id}-retest`}
          >
            {retesting ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <RotateCw className="size-3" />
            )}
          </Button>
        )}
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
              aria-label={tExtra('deleteModelConfirm')}
              data-testid={`model-row-${model.model_id}-confirm-delete`}
            >
              <Check className="size-3" />
            </button>
            <button
              type="button"
              className="rounded p-0.5 text-muted-foreground hover:bg-muted"
              onClick={() => setConfirmOpen(false)}
              aria-label={tExtra('deleteModelCancel')}
            >
              <X className="size-3" />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
