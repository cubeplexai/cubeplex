'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Loader2, Play, RotateCw } from 'lucide-react'
import {
  parseTestStream,
  enableTestedModels,
  startTestStream,
  type ApiClient,
  type ProbeResult,
  type ProbeStep,
} from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { LivenessRow } from './LivenessRow'
import { ModelTestCard, type ModelTestState } from './ModelTestCard'

interface TestStepProps {
  client: ApiClient
  providerId: string
  modelDbIds: string[]
  /** Map of model db id → display label, carried from the models step. */
  modelLabels?: Record<string, string>
  onFinish: () => void
}

type ModelEventData = ProbeResult & { model_db_id: string; display_name?: string }

export function TestStep({ client, providerId, modelDbIds, modelLabels, onFinish }: TestStepProps) {
  const t = useTranslations('adminModels.wizard.test')
  const tw = useTranslations('adminModels.wizard')

  const [liveness, setLiveness] = useState<ProbeStep | null>(null)
  const [cards, setCards] = useState<ModelTestState[]>([])
  const [running, setRunning] = useState(false)
  const [done, setDone] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeIds, setActiveIds] = useState<string[]>([])
  const controller = useRef<AbortController | null>(null)

  const run = useCallback(
    async (ids = modelDbIds) => {
      controller.current?.abort()
      const attempt = new AbortController()
      controller.current = attempt
      setRunning(true)
      setDone(false)
      setError(null)
      setLiveness(null)
      setActiveIds(ids)
      setCards((prev) => prev.filter((card) => !ids.includes(card.model_db_id)))
      try {
        const stream = await startTestStream(client, providerId, ids, attempt.signal)
        for await (const e of parseTestStream(stream)) {
          if (attempt.signal.aborted) break
          if (e.event === 'liveness') {
            setLiveness(e.data as ProbeStep)
          } else if (e.event === 'model') {
            const data = e.data as ModelEventData
            setCards((prev) => {
              const next: ModelTestState = {
                ...data,
                display_name:
                  data.display_name ?? modelLabels?.[data.model_db_id] ?? data.model_db_id,
              }
              const idx = prev.findIndex((c) => c.model_db_id === data.model_db_id)
              if (idx === -1) return [...prev, next]
              const copy = [...prev]
              copy[idx] = next
              return copy
            })
          } else if (e.event === 'done') {
            setDone(true)
          }
        }
      } catch (err) {
        if (!attempt.signal.aborted) setError((err as Error).message)
      } finally {
        if (controller.current === attempt) {
          setRunning(false)
          setDone(true)
        }
      }
    },
    [client, providerId, modelDbIds, modelLabels],
  )

  useEffect(() => {
    let disposed = false
    queueMicrotask(() => {
      if (!disposed) void run()
    })
    return () => {
      disposed = true
      controller.current?.abort()
    }
  }, [run])

  const livenessOk = liveness?.status === 'pass' || liveness?.status === 'warn'
  const usableModels = cards.filter(
    (c) =>
      c.current !== false && !c.blocking_failed && (c.overall === 'pass' || c.overall === 'warn'),
  )
  const canSave = livenessOk && usableModels.length > 0 && !running && !saving

  async function handleSave() {
    if (!canSave) return
    setSaving(true)
    setError(null)
    try {
      for (let i = 0; i < usableModels.length; i += 100) {
        const result = await enableTestedModels(
          client,
          providerId,
          usableModels.slice(i, i + 100).map((c) => c.model_db_id),
        )
        const blocked = result.results.filter((r) => r.status === 'blocked')
        if (blocked.length) throw new Error(blocked.map((r) => r.reason).join('; '))
      }
      onFinish()
    } catch (err) {
      setError((err as Error).message || t('saveFailed'))
      setSaving(false)
    }
  }

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{t('heading')}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={running}
          onClick={() => void run()}
        >
          {running ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : done ? (
            <RotateCw className="size-3.5" />
          ) : (
            <Play className="size-3.5" />
          )}
          {running ? t('running') : done ? t('rerun') : t('run')}
        </Button>
      </div>

      <LivenessRow step={liveness} running={running} />

      <div className="flex flex-col gap-2">
        {running &&
          activeIds
            .filter((id) => !cards.some((card) => card.model_db_id === id))
            .map((id) => (
              <div
                key={id}
                className="flex items-center gap-2 rounded-lg border border-border px-3 py-2.5"
              >
                <Loader2
                  aria-hidden
                  className="size-4 shrink-0 animate-spin text-muted-foreground"
                />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{modelLabels?.[id] ?? id}</p>
                  <p role="status" className="text-xs text-muted-foreground">
                    {livenessOk ? t('modelPending') : t('waitingLiveness')}
                  </p>
                </div>
              </div>
            ))}
        {cards.map((c) => (
          <ModelTestCard key={c.model_db_id} state={c} onRetest={() => void run([c.model_db_id])} />
        ))}
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">{t('saveHint')}</p>
        <Button type="button" size="sm" disabled={!canSave} onClick={() => void handleSave()}>
          {saving ? t('saving') : tw('finish')}
        </Button>
      </div>
    </div>
  )
}
