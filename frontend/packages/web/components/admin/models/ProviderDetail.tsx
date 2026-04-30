'use client'

import { useState } from 'react'
import { Pencil, Cable, Trash2, Box, Plus, ToggleLeft } from 'lucide-react'
import type {
  Provider,
  Model,
  OrgLLMSettings,
  OrgLLMSettingsUpdate,
  ApiClient,
  ModelCreate,
  ModelUpdate,
  ProviderUpdate,
  TestResult,
} from '@cubebox/core'
import { ProviderLogo } from './ProviderLogo'
import { ProviderFormDialog } from './ProviderFormDialog'
import { ModelFormDialog } from './ModelFormDialog'
import { ModelRow } from './ModelRow'
import { TestConnectionResult } from './TestConnectionResult'
import { OrgModelSettings } from './OrgModelSettings'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Separator } from '@/components/ui/separator'

interface ProviderDetailProps {
  provider: Provider & { models?: Model[] }
  models: Model[]
  settings: OrgLLMSettings | null
  client: ApiClient
  onUpdateProvider: (client: ApiClient, id: string, body: ProviderUpdate) => Promise<void>
  onDeleteProvider: (client: ApiClient, id: string) => Promise<void>
  onTestConnection: (
    client: ApiClient,
    body: { provider_type: string; base_url: string; api_key?: string | null; auth_type: string },
  ) => Promise<TestResult>
  onToggleOverride: (client: ApiClient, providerId: string, enabled: boolean) => Promise<void>
  onCreateModel: (client: ApiClient, providerId: string, body: ModelCreate) => Promise<Model>
  onUpdateModel: (
    client: ApiClient,
    providerId: string,
    modelId: string,
    body: ModelUpdate,
  ) => Promise<void>
  onDeleteModel: (client: ApiClient, providerId: string, modelId: string) => Promise<void>
  onUpdateSettings: (client: ApiClient, body: OrgLLMSettingsUpdate) => Promise<void>
}

function authTypeLabel(authType: string): string {
  const labels: Record<string, string> = {
    api_key: 'API Key',
    bearer_token: 'Bearer Token',
    oauth: 'OAuth 2.0',
    none: '无认证',
  }
  return labels[authType] ?? authType
}

export function ProviderDetail({
  provider,
  models,
  settings,
  client,
  onUpdateProvider,
  onDeleteProvider,
  onTestConnection,
  onToggleOverride,
  onCreateModel,
  onUpdateModel,
  onDeleteModel,
  onUpdateSettings,
}: ProviderDetailProps) {
  const [editOpen, setEditOpen] = useState(false)
  const [modelFormOpen, setModelFormOpen] = useState(false)
  const [editingModel, setEditingModel] = useState<Model | null>(null)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [overrideBusy, setOverrideBusy] = useState(false)

  const isSystem = provider.is_system
  const hasOverride = provider.org_override?.enabled ?? false

  async function handleTest(): Promise<void> {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await onTestConnection(client, {
        provider_type: provider.provider_type,
        base_url: provider.base_url,
        api_key: null,
        auth_type: provider.auth_type,
      })
      setTestResult(result)
    } catch (e) {
      setTestResult({ ok: false, error: (e as Error).message, latency_ms: 0 })
    } finally {
      setTesting(false)
    }
  }

  async function handleDelete(): Promise<void> {
    if (!confirm('确定删除此 provider？此操作不可撤销。')) return
    setDeleting(true)
    try {
      await onDeleteProvider(client, provider.id)
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  async function handleToggleOverride(): Promise<void> {
    setOverrideBusy(true)
    try {
      await onToggleOverride(client, provider.id, !hasOverride)
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setOverrideBusy(false)
    }
  }

  async function handleEditSave(body: ProviderUpdate): Promise<void> {
    await onUpdateProvider(client, provider.id, body)
    setEditOpen(false)
  }

  async function handleCreateModel(body: ModelCreate): Promise<void> {
    await onCreateModel(client, provider.id, body)
    setModelFormOpen(false)
    setEditingModel(null)
  }

  async function handleUpdateModel(body: ModelUpdate): Promise<void> {
    if (!editingModel) return
    await onUpdateModel(client, provider.id, editingModel.id, body)
    setModelFormOpen(false)
    setEditingModel(null)
  }

  async function handleDeleteModel(model: Model): Promise<void> {
    if (!confirm(`确定删除模型 ${model.model_id}？`)) return
    try {
      await onDeleteModel(client, provider.id, model.id)
    } catch (e) {
      alert((e as Error).message)
    }
  }

  const providerModels = provider.models ?? models

  return (
    <div className="flex w-full flex-col gap-5 p-6" data-testid="provider-detail-panel">
      {/* Header */}
      <header className="flex items-start gap-4">
        <ProviderLogo name={provider.name} logoUrl={provider.logo_url} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-xl font-semibold tracking-tight">{provider.name}</h3>
            {isSystem && (
              <Badge variant="secondary" className="text-[11px]">
                系统
              </Badge>
            )}
          </div>

          {/* Info row */}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{provider.provider_type}</span>
            <span className="text-border/60">&middot;</span>
            <span className="font-mono text-[11px]">{provider.base_url}</span>
            <span className="text-border/60">&middot;</span>
            <span>{authTypeLabel(provider.auth_type)}</span>
          </div>

          {/* API key status */}
          <div className="mt-1 text-xs">
            {provider.has_api_key ? (
              <span className="text-muted-foreground">
                **** <span className="text-emerald-500">(已设置)</span>
              </span>
            ) : (
              <span className="text-muted-foreground/60">未设置 API Key</span>
            )}
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2 shrink-0">
          {!isSystem && (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEditOpen(true)}
                className="gap-1.5"
              >
                <Pencil className="size-3.5" />
                编辑
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void handleTest()}
                disabled={testing}
                className="gap-1.5"
              >
                <Cable className="size-3.5" />
                测试连接
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => void handleDelete()}
                disabled={deleting}
                className="gap-1.5"
              >
                <Trash2 className="size-3.5" />
                删除
              </Button>
            </>
          )}
          {isSystem && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <ToggleLeft className="size-4" />
              <span>系统 provider 不可删除</span>
            </div>
          )}
        </div>
      </header>

      {/* Test Result */}
      {testResult && (
        <div className="mb-2">
          <TestConnectionResult result={testResult} busy={testing} />
        </div>
      )}

      {/* Override toggle */}
      {!isSystem && (
        <div className="flex items-center gap-3 rounded-lg border border-border/70 bg-card/40 px-4 py-3">
          <Switch
            id="provider-override"
            checked={hasOverride}
            onCheckedChange={() => void handleToggleOverride()}
            disabled={overrideBusy}
          />
          <label htmlFor="provider-override" className="flex cursor-pointer flex-col gap-0.5">
            <span className="text-sm font-medium">组织覆盖</span>
            <span className="text-xs text-muted-foreground">
              启用后将使用组织配置覆盖此 provider 的默认配置
            </span>
          </label>
        </div>
      )}

      <Separator />

      {/* Models Section */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-sm font-medium">模型 ({providerModels.length})</h4>
          {!isSystem && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setEditingModel(null)
                setModelFormOpen(true)
              }}
              className="gap-1.5"
            >
              <Plus className="size-3.5" />
              添加模型
            </Button>
          )}
        </div>

        {providerModels.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/60 py-10 text-center">
            <Box className="size-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">暂无模型</p>
            {!isSystem && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setEditingModel(null)
                  setModelFormOpen(true)
                }}
                className="mt-1 gap-1.5"
              >
                <Plus className="size-3.5" />
                添加模型
              </Button>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {providerModels.map((model) => (
              <ModelRow
                key={model.id}
                model={model}
                onEdit={(m) => {
                  setEditingModel(m)
                  setModelFormOpen(true)
                }}
                onDelete={(m) => void handleDeleteModel(m)}
              />
            ))}
          </div>
        )}
      </section>

      <Separator />

      {/* Org Settings Section */}
      <section>
        <OrgModelSettings
          providers={[provider]}
          models={providerModels}
          settings={settings}
          client={client}
          onUpdateSettings={onUpdateSettings}
        />
      </section>

      {/* Dialogs */}
      <ProviderFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        provider={provider}
        client={client}
        onTestConnection={onTestConnection}
        onSave={(body) => handleEditSave(body as ProviderUpdate)}
      />

      <ModelFormDialog
        open={modelFormOpen}
        onOpenChange={(open) => {
          setModelFormOpen(open)
          if (!open) setEditingModel(null)
        }}
        model={editingModel}
        onSave={
          editingModel
            ? (body) => handleUpdateModel(body as ModelUpdate)
            : (body) => handleCreateModel(body as ModelCreate)
        }
      />
    </div>
  )
}
