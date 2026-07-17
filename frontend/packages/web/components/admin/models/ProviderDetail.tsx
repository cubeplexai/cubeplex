'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'

const PRESETS_ADMIN_PATH = '/admin/presets'
import { Box, Check, Loader2, Pencil, Plus, RotateCw, Trash2, Zap, X } from 'lucide-react'
import {
  ApiError,
  checkLiveness,
  parseTestStream,
  startTestStream,
  type ApiClient,
  type Model,
  type ModelCreate,
  type ModelUpdate,
  type Provider,
  type ProviderUpdate,
} from '@cubeplex/core'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { ProviderLogo } from './ProviderLogo'
import { ProviderFormDialog } from './ProviderFormDialog'
import { ModelFormDialog } from './ModelFormDialog'
import { ModelRow } from './ModelRow'

/**
 * Backend's ModelInUseByPresetError carries refs in two places:
 *   - `error.data.refs` — structured payload (preferred).
 *   - `details` — Python-repr fallback like
 *     "refs=[{'org_id': '…', 'preset_label': '…', 'source': '…'}, …]".
 * Prefer the structured form; fall back to the regex parser for old
 * responses (e.g. an admin's cached browser tab hitting an older server).
 */
export interface PresetRef {
  org_id: string
  preset_label: string
  source?: 'org' | 'system'
}

function isPresetRef(v: unknown): v is PresetRef {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  return typeof o.org_id === 'string' && typeof o.preset_label === 'string'
}

export function extractPresetRefs(data: unknown): PresetRef[] {
  if (typeof data !== 'object' || data === null) return []
  const refs = (data as { refs?: unknown }).refs
  if (!Array.isArray(refs)) return []
  return refs.filter(isPresetRef)
}

export function parsePresetRefs(details: string | null | undefined): PresetRef[] {
  if (!details) return []
  const refs: PresetRef[] = []
  const re = /'org_id'\s*:\s*'([^']+)'\s*,\s*'preset_label'\s*:\s*'([^']+)'/g
  let m: RegExpExecArray | null
  while ((m = re.exec(details)) !== null) {
    refs.push({ org_id: m[1], preset_label: m[2] })
  }
  return refs
}

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
  onRefresh?: () => void
}

function livenessDotClass(status: string | null | undefined): string {
  if (status === 'ok' || status === 'pass') return 'bg-success-solid'
  if (status === 'fail' || status === 'error') return 'bg-danger-solid'
  return 'bg-faint'
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
  onRefresh,
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
  const [modelInUseRefs, setModelInUseRefs] = useState<PresetRef[] | null>(null)
  const [testingAll, setTestingAll] = useState(false)
  const [testingConn, setTestingConn] = useState(false)

  const isSystem = provider.is_system

  async function handleTestAll() {
    if (models.length === 0) return
    setTestingAll(true)
    setModelError(null)
    try {
      const stream = await startTestStream(
        client,
        provider.id,
        models.map((m) => m.id),
      )
      // Drain the stream so the backend records each model result.
      for await (const _e of parseTestStream(stream)) {
        void _e
      }
      onRefresh?.()
    } catch (e) {
      setModelError((e as Error).message)
    } finally {
      setTestingAll(false)
    }
  }

  async function handleTestConnection() {
    if (models.length === 0) return
    setTestingConn(true)
    setModelError(null)
    try {
      await checkLiveness(client, provider.id, models[0].model_id)
      onRefresh?.()
    } catch (e) {
      setModelError((e as Error).message)
    } finally {
      setTestingConn(false)
    }
  }

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
    setModelInUseRefs(null)
    try {
      await onDeleteModel(client, provider.id, model.id)
    } catch (e) {
      if (e instanceof ApiError && e.code === 'model_in_use_by_preset') {
        // Structured `data.refs` is the source of truth; fall back to the
        // Python-repr regex parser for responses from older servers.
        let refs = extractPresetRefs(e.data)
        if (refs.length === 0) {
          const detailStr = typeof e.detail === 'string' ? e.detail : null
          refs = parsePresetRefs(detailStr)
        }
        setModelInUseRefs(refs)
        setModelError(null)
        return
      }
      setModelError((e as Error).message)
    }
  }

  return (
    <div className="flex w-full flex-col gap-5 p-6" data-testid="provider-detail-panel">
      <header className="flex items-start gap-4">
        <ProviderLogo
          name={provider.name}
          logoUrl={provider.logo_url}
          logo={provider.logo}
          size="lg"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'size-2.5 shrink-0 rounded-full',
                livenessDotClass(provider.last_liveness_status),
              )}
              title={t('livenessStatus')}
              data-testid="provider-liveness-dot"
            />
            <h3 className="text-xl font-semibold tracking-tight">{provider.name}</h3>
            <code className="text-xs text-muted-foreground">{provider.slug}</code>
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
              <span className="text-success-fg">{t('apiKeySet')}</span>
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
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleTestConnection()}
              disabled={testingConn || models.length === 0}
              className="gap-1.5"
              data-testid="provider-test-connection"
            >
              {testingConn ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Zap className="size-3.5" />
              )}
              {t('testConnection')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleTestAll()}
              disabled={testingAll || models.length === 0}
              className="gap-1.5"
              data-testid="provider-test-all"
            >
              {testingAll ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <RotateCw className="size-3.5" />
              )}
              {t('testAll')}
            </Button>
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
        </div>

        {modelError && (
          <div className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            {modelError}
          </div>
        )}

        {modelInUseRefs && (
          <div
            className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive"
            data-testid="model-in-use-by-preset-error"
            role="alert"
          >
            <div className="font-medium">{t('modelInUseByPreset.title')}</div>
            <p className="mt-1 text-destructive/90">
              {t('modelInUseByPreset.body')}{' '}
              <Link
                href={PRESETS_ADMIN_PATH}
                className="underline underline-offset-2 hover:text-destructive"
              >
                {t('modelInUseByPreset.linkLabel')}
              </Link>
            </p>
            {modelInUseRefs.length > 0 && (
              <ul className="mt-1.5 list-disc space-y-0.5 pl-5">
                {modelInUseRefs.map((r) => (
                  <li key={`${r.org_id}:${r.preset_label}`}>
                    <code className="font-mono text-[11px]">{r.preset_label}</code>
                    {r.source && (
                      <span className="ml-1 text-xs text-muted-foreground">
                        {r.source === 'system'
                          ? t('modelInUseByPreset.systemSourceLabel')
                          : t('modelInUseByPreset.orgSourceLabel')}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {modelInUseRefs.some((r) => r.source === 'system') && (
              <p className="mt-2 text-destructive/90">
                {t('modelInUseByPreset.systemHint', { path: PRESETS_ADMIN_PATH })}
              </p>
            )}
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
                client={client}
                providerId={provider.id}
                onEdit={(model) => {
                  setEditingModel(model)
                  setModelError(null)
                  setModelFormOpen(true)
                }}
                onDelete={(model) => void handleDeleteModel(model)}
                onRetested={() => onRefresh?.()}
              />
            ))}
          </div>
        )}
      </section>

      <ProviderFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        provider={provider}
        onSave={handleEditSave}
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
