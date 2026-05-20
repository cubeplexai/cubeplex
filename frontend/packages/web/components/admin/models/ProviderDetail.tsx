'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Box, Check, Pencil, Plus, Trash2, X } from 'lucide-react'
import type {
  ApiClient,
  Model,
  ModelCreate,
  ModelUpdate,
  Provider,
  ProviderUpdate,
} from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { ProviderLogo } from './ProviderLogo'
import { ProviderFormDialog } from './ProviderFormDialog'
import { ModelFormDialog } from './ModelFormDialog'
import { ModelRow } from './ModelRow'

interface ProviderDetailProps {
  provider: Provider
  models: Model[]
  modelsLoading: boolean
  modelsError: string | null
  client: ApiClient
  onUpdateProvider: (client: ApiClient, id: string, body: ProviderUpdate) => Promise<void>
  onDeleteProvider: (client: ApiClient, id: string) => Promise<void>
  onCreateModel: (client: ApiClient, providerId: string, body: ModelCreate) => Promise<Model>
  onUpdateModel: (
    client: ApiClient,
    providerId: string,
    modelId: string,
    body: ModelUpdate,
  ) => Promise<void>
  onDeleteModel: (client: ApiClient, providerId: string, modelId: string) => Promise<void>
}

function authTypeLabel(
  t: (key: 'authApiKey' | 'authBearer' | 'authOAuth' | 'authNone') => string,
  authType: string,
): string {
  switch (authType) {
    case 'api_key':
      return t('authApiKey')
    case 'bearer_token':
      return t('authBearer')
    case 'oauth':
      return t('authOAuth')
    case 'none':
      return t('authNone')
    default:
      return authType
  }
}

export function ProviderDetail({
  provider,
  models,
  modelsLoading,
  modelsError,
  client,
  onUpdateProvider,
  onDeleteProvider,
  onCreateModel,
  onUpdateModel,
  onDeleteModel,
}: ProviderDetailProps) {
  const t = useTranslations('adminModels')
  const tExtra = useTranslations('adminModelsExtra')
  const [editOpen, setEditOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [modelFormOpen, setModelFormOpen] = useState(false)
  const [editingModel, setEditingModel] = useState<Model | null>(null)
  const [modelError, setModelError] = useState<string | null>(null)

  const isSystem = provider.is_system

  async function handleDelete() {
    setDeleting(true)
    setDeleteError(null)
    try {
      await onDeleteProvider(client, provider.id)
    } catch (e) {
      setDeleteError((e as Error).message)
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  async function handleEditSave(body: ProviderUpdate): Promise<void> {
    await onUpdateProvider(client, provider.id, body)
    setEditOpen(false)
  }

  async function handleCreateModel(body: ModelCreate): Promise<void> {
    setModelError(null)
    try {
      await onCreateModel(client, provider.id, body)
      setModelFormOpen(false)
      setEditingModel(null)
    } catch (e) {
      setModelError((e as Error).message)
      throw e
    }
  }

  async function handleUpdateModel(body: ModelUpdate): Promise<void> {
    if (!editingModel) return
    setModelError(null)
    try {
      await onUpdateModel(client, provider.id, editingModel.id, body)
      setModelFormOpen(false)
      setEditingModel(null)
    } catch (e) {
      setModelError((e as Error).message)
      throw e
    }
  }

  async function handleDeleteModel(model: Model): Promise<void> {
    setModelError(null)
    try {
      await onDeleteModel(client, provider.id, model.id)
    } catch (e) {
      setModelError((e as Error).message)
    }
  }

  return (
    <div className="flex w-full flex-col gap-5 p-6" data-testid="provider-detail-panel">
      <header className="flex items-start gap-4">
        <ProviderLogo name={provider.name} logoUrl={provider.logo_url} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-xl font-semibold tracking-tight">{provider.name}</h3>
            {isSystem && (
              <Badge variant="secondary" className="text-[11px]">
                {t('systemBadge')}
              </Badge>
            )}
          </div>

          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{provider.provider_type}</span>
            <span className="text-border/60">·</span>
            <span className="font-mono text-[11px]">{provider.base_url}</span>
            <span className="text-border/60">·</span>
            <span>{authTypeLabel(t, provider.auth_type)}</span>
          </div>

          <div className="mt-1 text-xs">
            {provider.has_api_key ? (
              <span className="text-emerald-600 dark:text-emerald-400">{t('apiKeySet')}</span>
            ) : (
              <span className="text-muted-foreground/60">{t('apiKeyMissing')}</span>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {!isSystem && !confirmDelete && (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEditOpen(true)}
                className="gap-1.5"
              >
                <Pencil className="size-3.5" />
                {t('edit')}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setDeleteError(null)
                  setConfirmDelete(true)
                }}
                className="gap-1.5 text-destructive hover:bg-destructive/10 hover:text-destructive"
                data-testid="provider-delete-button"
              >
                <Trash2 className="size-3.5" />
                {t('delete')}
              </Button>
            </>
          )}
          {!isSystem && confirmDelete && (
            <div className="flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5">
              <span className="text-xs text-destructive">{t('deleteConfirm')}</span>
              <button
                type="button"
                className="rounded p-0.5 text-destructive hover:bg-destructive/20"
                disabled={deleting}
                onClick={() => void handleDelete()}
                aria-label={tExtra('deleteProviderConfirm')}
                data-testid="provider-delete-confirm"
              >
                <Check className="size-3.5" />
              </button>
              <button
                type="button"
                className="rounded p-0.5 text-muted-foreground hover:bg-muted"
                onClick={() => setConfirmDelete(false)}
                aria-label={tExtra('deleteProviderCancel')}
              >
                <X className="size-3.5" />
              </button>
            </div>
          )}
          {isSystem && <span className="text-xs text-muted-foreground">{t('systemReadonly')}</span>}
        </div>
      </header>

      {deleteError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {deleteError}
        </div>
      )}

      <Separator />

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h4 className="text-sm font-medium">{t('modelsHeading', { count: models.length })}</h4>
          {!isSystem && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setEditingModel(null)
                setModelError(null)
                setModelFormOpen(true)
              }}
              className="gap-1.5"
            >
              <Plus className="size-3.5" />
              {t('addModel')}
            </Button>
          )}
        </div>

        {modelError && (
          <div className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            {modelError}
          </div>
        )}

        {modelsLoading ? (
          <div className="flex items-center justify-center rounded-lg border border-dashed border-border/60 py-8 text-xs text-muted-foreground">
            {t('loading')}
          </div>
        ) : modelsError ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
            {t('loadFailed', { message: modelsError })}
          </div>
        ) : models.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/60 py-10 text-center">
            <Box className="size-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">{t('noModels')}</p>
            {!isSystem && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setEditingModel(null)
                  setModelError(null)
                  setModelFormOpen(true)
                }}
                className="mt-1 gap-1.5"
              >
                <Plus className="size-3.5" />
                {t('addModel')}
              </Button>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {models.map((m) => (
              <ModelRow
                key={m.id}
                model={m}
                onEdit={(model) => {
                  setEditingModel(model)
                  setModelError(null)
                  setModelFormOpen(true)
                }}
                onDelete={(model) => void handleDeleteModel(model)}
              />
            ))}
          </div>
        )}
      </section>

      <ProviderFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        provider={provider}
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
