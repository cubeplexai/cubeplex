'use client'

import { useEffect, useMemo, useState } from 'react'
import { createApiClient, fetchProvider, fetchProviders } from '@cubebox/core'
import type { Model, Provider } from '@cubebox/core'

export interface ProviderModelOption {
  providerId: string
  providerName: string
  providerLogoUrl: string | null
  modelId: string
  displayName: string
  /** Reference stored in OrgLLMSettings: `${providerName}/${modelId}` */
  ref: string
}

interface UseAllModelsResult {
  providers: Provider[]
  options: ProviderModelOption[]
  loading: boolean
  error: Error | null
}

/**
 * Loads all providers and their models so the org-level settings page can offer
 * cross-provider model pickers. Performs N+1 fetches but admin-only and small N.
 */
export function useAllModels(): UseAllModelsResult {
  const client = useMemo(() => createApiClient(''), [])
  const [providers, setProviders] = useState<Provider[]>([])
  const [modelsByProvider, setModelsByProvider] = useState<Record<string, Model[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const list = await fetchProviders(client)
        if (cancelled) return
        setProviders(list)
        const detailed = await Promise.all(list.map((p) => fetchProvider(client, p.id)))
        if (cancelled) return
        const map: Record<string, Model[]> = {}
        for (const p of detailed) map[p.id] = p.models ?? []
        setModelsByProvider(map)
      } catch (e) {
        if (!cancelled) setError(e as Error)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [client])

  const options: ProviderModelOption[] = useMemo(() => {
    const out: ProviderModelOption[] = []
    for (const p of providers) {
      const models = modelsByProvider[p.id] ?? []
      for (const m of models) {
        if (!m.enabled) continue
        out.push({
          providerId: p.id,
          providerName: p.name,
          providerLogoUrl: p.logo_url,
          modelId: m.model_id,
          displayName: m.display_name || m.model_id,
          ref: `${p.name}/${m.model_id}`,
        })
      }
    }
    return out
  }, [providers, modelsByProvider])

  return { providers, options, loading, error }
}
