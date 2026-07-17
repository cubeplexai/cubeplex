'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient } from '@cubeplex/core'
import type { Provider, Model } from '@cubeplex/core'

import { fetchAdminModelPresets, type AdminModelPresetsResponse } from '@/lib/api/presets'

import { PresetEditor } from './PresetEditor'

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; data: AdminModelPresetsResponse; availableModels: string[] }

/**
 * Admin Model Presets page (shell).
 *
 * Loads the initial preset row + the catalog of available `slug/model_id`
 * refs that fill the per-preset chain autocomplete. The actual editing UX
 * lives in {@link PresetEditor} (client component) so the shell stays small
 * and easy to swap out (e.g. for SSR later).
 */
export default function AdminPresetsPage(): React.ReactElement {
  const t = useTranslations('adminPresets')
  const client = useMemo(() => createApiClient(''), [])
  const [state, setState] = useState<LoadState>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false

    async function load(): Promise<void> {
      try {
        const [presetsRes, providersRes] = await Promise.all([
          fetchAdminModelPresets(),
          client.get('/api/v1/admin/providers'),
        ])
        if (!providersRes.ok) {
          throw new Error(`providers HTTP ${providersRes.status}`)
        }
        const providers = (await providersRes.json()) as Provider[]
        // Fetch per-provider details to get the model list (the list endpoint
        // returns only model_count). Concurrency-bounded by Promise.all over
        // a typically-small provider set.
        const detailResponses = await Promise.all(
          providers.map((p) => client.get(`/api/v1/admin/providers/${p.id}`)),
        )
        const availableModels: string[] = []
        for (let i = 0; i < providers.length; i += 1) {
          const res = detailResponses[i]
          if (!res.ok) continue
          const detail = (await res.json()) as Provider & { models?: Model[] }
          const slug = providers[i].slug
          for (const m of detail.models ?? []) {
            availableModels.push(`${slug}/${m.model_id}`)
          }
        }
        availableModels.sort()
        if (cancelled) return
        setState({ kind: 'ready', data: presetsRes, availableModels })
      } catch (err) {
        if (cancelled) return
        setState({ kind: 'error', message: (err as Error).message })
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [client])

  if (state.kind === 'loading') {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t('loading')}
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <div className="flex h-full items-center justify-center text-sm text-destructive">
        {t('loadFailed', { message: state.message })}
      </div>
    )
  }
  return <PresetEditor initial={state.data} availableModels={state.availableModels} />
}
