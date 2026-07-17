'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { createApiClient, useModelsStore, useProvidersStore } from '@cubeplex/core'
import { ModelsToolbar, type ProviderKind } from '@/components/admin/models/ModelsToolbar'
import { ProviderList } from '@/components/admin/models/ProviderList'
import { ProviderDetail } from '@/components/admin/models/ProviderDetail'
import { PageHeader } from '@/components/management/PageHeader'

export default function ModelsPage() {
  const t = useTranslations('adminModels')
  const router = useRouter()
  const client = useMemo(() => createApiClient(''), [])
  const {
    providers,
    selectedId,
    loading,
    error,
    fetchProviders,
    selectProvider,
    updateProvider,
    deleteProvider,
  } = useProvidersStore()
  const {
    models,
    loading: modelsLoading,
    error: modelsError,
    fetchModels,
    clearModels,
    createModel,
    updateModel,
    deleteModel,
  } = useModelsStore()

  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<ProviderKind>('all')

  useEffect(() => {
    void fetchProviders(client)
  }, [client, fetchProviders])

  useEffect(() => {
    if (selectedId) {
      void fetchModels(client, selectedId)
    } else {
      clearModels()
    }
  }, [selectedId, client, fetchModels, clearModels])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return providers.filter((p) => {
      if (kind === 'system' && !p.is_system) return false
      if (kind === 'custom' && p.is_system) return false
      if (q && !p.name.toLowerCase().includes(q)) return false
      return true
    })
  }, [providers, query, kind])

  const selectedProvider = providers.find((p) => p.id === selectedId) ?? null

  return (
    <div className="flex h-full flex-col">
      <PageHeader title={t('title')} description={t('subtitle')} />

      <ModelsToolbar
        query={query}
        kind={kind}
        onQueryChange={setQuery}
        onKindChange={setKind}
        onAddClick={() => router.push('/admin/models/new')}
      />

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label="provider-list"
          className="w-[320px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          <ProviderList
            providers={filtered}
            loading={loading}
            error={error}
            selectedId={selectedId}
            onSelect={selectProvider}
          />
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          {selectedProvider ? (
            <ProviderDetail
              provider={selectedProvider}
              models={models}
              modelsLoading={modelsLoading}
              modelsError={modelsError}
              client={client}
              onUpdateProvider={updateProvider}
              onDeleteProvider={deleteProvider}
              onCreateModel={createModel}
              onUpdateModel={updateModel}
              onDeleteModel={deleteModel}
              onRefresh={() => {
                void fetchProviders(client)
                if (selectedId) void fetchModels(client, selectedId)
              }}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-sm text-muted-foreground">
              {t('selectProvider')}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
