'use client'

import { useEffect, useMemo } from 'react'
import {
  useProvidersStore,
  useModelsStore,
  useOrgModelSettingsStore,
  createApiClient,
} from '@cubebox/core'
import { ProviderList } from '@/components/admin/models/ProviderList'
import { ProviderDetail } from '@/components/admin/models/ProviderDetail'

export default function ModelsPage() {
  const client = useMemo(() => createApiClient(''), [])
  const {
    providers,
    selectedId,
    loading,
    fetchProviders,
    createProvider,
    updateProvider,
    deleteProvider,
    testConnection,
    selectProvider,
    toggleOverride,
  } = useProvidersStore()
  const { models, fetchModels, createModel, updateModel, deleteModel } = useModelsStore()
  const { settings, fetchSettings, updateSettings } = useOrgModelSettingsStore()

  useEffect(() => {
    fetchProviders(client)
    fetchSettings(client)
  }, [client, fetchProviders, fetchSettings])

  useEffect(() => {
    if (selectedId) {
      fetchModels(client, selectedId)
    }
  }, [selectedId, client, fetchModels])

  const selectedProvider = providers.find((p) => p.id === selectedId) || null

  return (
    <div className="flex h-full">
      <div className="w-72 border-r shrink-0 flex flex-col">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="font-semibold text-sm">Providers</h2>
        </div>
        <ProviderList
          providers={providers}
          selectedId={selectedId}
          loading={loading}
          onSelect={selectProvider}
          onCreateProvider={createProvider}
          onTestConnection={testConnection}
          client={client}
        />
      </div>
      <div className="flex-1 overflow-y-auto">
        {selectedProvider ? (
          <ProviderDetail
            provider={selectedProvider}
            models={models}
            settings={settings}
            client={client}
            onUpdateProvider={updateProvider}
            onDeleteProvider={deleteProvider}
            onTestConnection={testConnection}
            onToggleOverride={toggleOverride}
            onCreateModel={createModel}
            onUpdateModel={updateModel}
            onDeleteModel={deleteModel}
            onUpdateSettings={updateSettings}
          />
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground">
            选择一个 provider 查看详情
          </div>
        )}
      </div>
    </div>
  )
}
