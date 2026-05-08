'use client'

import { useEffect, useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import type { ApiClient, MCPAuthMethod, MCPCatalogConnector } from '@cubebox/core'
import { useMcpStore, useWorkspaceMcpStore } from '@cubebox/core'
import { ExternalLink, Globe, Loader2, X } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { MCPStaticForm } from './MCPStaticForm'

const OAUTH_ORIGIN_KEY = 'mcp_oauth_origin'

export interface MCPInstallDrawerProps {
  connector: MCPCatalogConnector | null
  mode: 'admin' | 'workspace'
  open: boolean
  onClose: () => void
  client: ApiClient
  wsId: string
}

function readMetadataString(metadata: Record<string, unknown>, key: string): string | null {
  const raw = metadata[key]
  return typeof raw === 'string' && raw.length > 0 ? raw : null
}

function defaultAuthMethod(supported: MCPAuthMethod[]): MCPAuthMethod {
  if (supported.includes('oauth')) return 'oauth'
  if (supported.includes('static')) return 'static'
  return 'none'
}

interface ActionTabContentProps {
  notice: string
  buttonLabel: string
  submitting: boolean
  onAction: () => void
}

function ActionTabContent({ notice, buttonLabel, submitting, onAction }: ActionTabContentProps) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">{notice}</p>
      <div className="flex justify-end">
        <Button type="button" disabled={submitting} onClick={onAction}>
          {submitting ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
          {buttonLabel}
        </Button>
      </div>
    </div>
  )
}

export function MCPInstallDrawer({
  connector,
  mode,
  open,
  onClose,
  client,
  wsId,
}: MCPInstallDrawerProps) {
  const t = useTranslations('mcpCatalog')

  // Store wiring picked by mode. Both stores expose the same surface for the
  // catalog actions we use here.
  const adminStore = useMcpStore()
  const wsStore = useWorkspaceMcpStore()
  const store = mode === 'admin' ? adminStore : wsStore
  const catalogError = store.catalogError

  const supported = connector?.supported_auth_methods ?? []
  const [activeMethod, setActiveMethod] = useState<MCPAuthMethod>('oauth')
  const [submitting, setSubmitting] = useState(false)

  // Reset method when connector changes / drawer reopens.
  useEffect(() => {
    if (open && connector) {
      setActiveMethod(defaultAuthMethod(connector.supported_auth_methods))
      setSubmitting(false)
    }
  }, [open, connector])

  if (!connector) return null

  const docsUrl = readMetadataString(connector.metadata, 'docs_url')
  const iconUrl = readMetadataString(connector.metadata, 'icon_url')

  function persistOAuthOrigin(): void {
    if (typeof window === 'undefined') return
    try {
      window.sessionStorage.setItem(
        OAUTH_ORIGIN_KEY,
        window.location.pathname + window.location.search,
      )
    } catch {
      // sessionStorage may be unavailable (SSR / privacy mode); non-fatal.
    }
  }

  async function handleOAuth(): Promise<void> {
    if (!connector) return
    setSubmitting(true)
    try {
      const installResult =
        mode === 'admin'
          ? await adminStore.installFromCatalog(client, wsId, connector.id, {
              auth_method: 'oauth',
            })
          : await wsStore.installFromCatalog(client, wsId, connector.id, {
              auth_method: 'oauth',
            })
      persistOAuthOrigin()
      const oauthResult =
        mode === 'admin'
          ? await adminStore.startOAuth(client, installResult.install_id)
          : await wsStore.startOAuth(client, wsId, installResult.install_id)
      if (typeof window !== 'undefined') {
        window.location.href = oauthResult.authorize_url
      }
    } catch {
      // catalogError is set inside the store; just stop the spinner.
      setSubmitting(false)
    }
  }

  async function handleStaticSubmit(values: Record<string, string>): Promise<void> {
    if (!connector) return
    setSubmitting(true)
    try {
      // Single-field forms only (see MCPStaticForm TODO). The single value is
      // the secret token; backend will wrap it in the right header at probe
      // time when template support lands.
      const fieldName = connector.static_form_fields?.[0]?.name ?? 'token'
      const credentialPlaintext = values[fieldName] ?? ''
      if (mode === 'admin') {
        await adminStore.installFromCatalog(client, wsId, connector.id, {
          auth_method: 'static',
          credential_plaintext: credentialPlaintext,
        })
      } else {
        await wsStore.installFromCatalog(client, wsId, connector.id, {
          auth_method: 'static',
          credential_plaintext: credentialPlaintext,
        })
      }
      onClose()
    } catch {
      setSubmitting(false)
    }
  }

  async function handleNoneInstall(): Promise<void> {
    if (!connector) return
    setSubmitting(true)
    try {
      if (mode === 'admin') {
        await adminStore.installFromCatalog(client, wsId, connector.id, {
          auth_method: 'none',
        })
      } else {
        await wsStore.installFromCatalog(client, wsId, connector.id, {
          auth_method: 'none',
        })
      }
      onClose()
    } catch {
      setSubmitting(false)
    }
  }

  const oauthNotice = mode === 'admin' ? t('adminOAuthNotice') : t('workspaceOAuthNotice')

  return (
    <DialogPrimitive.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200" />
        <DialogPrimitive.Popup className="fixed inset-y-0 right-0 z-50 flex h-full w-[min(560px,100vw)] flex-col border-l border-border bg-popover text-popover-foreground shadow-2xl data-[ending-style]:translate-x-full data-[starting-style]:translate-x-full transition-transform duration-200">
          <header className="flex items-start justify-between gap-3 border-b border-border p-5">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center overflow-hidden rounded-md border border-border bg-muted/40">
                {iconUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={iconUrl}
                    alt=""
                    width={32}
                    height={32}
                    className="size-8 object-contain"
                  />
                ) : (
                  <Globe className="size-5 text-muted-foreground" aria-hidden />
                )}
              </div>
              <div className="flex flex-col">
                <DialogPrimitive.Title className="text-base font-semibold">
                  {connector.name}
                </DialogPrimitive.Title>
                <span className="text-xs text-muted-foreground">{connector.provider}</span>
              </div>
            </div>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label={t('close')}
                  disabled={submitting}
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <X />
                </button>
              }
            />
          </header>

          <div className="flex-1 overflow-y-auto p-5">
            <div className="flex flex-col gap-5">
              <DialogPrimitive.Description className="text-sm text-muted-foreground">
                {connector.description}
              </DialogPrimitive.Description>

              {docsUrl ? (
                <a
                  href={docsUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
                >
                  {t('docsLink')}
                  <ExternalLink className="size-3.5" aria-hidden />
                </a>
              ) : null}

              {catalogError ? (
                <Alert variant="destructive">
                  <AlertTitle>{t('errorBannerTitle')}</AlertTitle>
                  <AlertDescription>{catalogError.message}</AlertDescription>
                </Alert>
              ) : null}

              <Tabs
                value={activeMethod}
                onValueChange={(value: unknown) => setActiveMethod(value as MCPAuthMethod)}
              >
                {supported.length > 1 ? (
                  <TabsList className="w-fit">
                    {supported.includes('oauth') ? (
                      <TabsTrigger value="oauth">{t('authOAuth')}</TabsTrigger>
                    ) : null}
                    {supported.includes('static') ? (
                      <TabsTrigger value="static">{t('authStatic')}</TabsTrigger>
                    ) : null}
                    {supported.includes('none') ? (
                      <TabsTrigger value="none">{t('authNone')}</TabsTrigger>
                    ) : null}
                  </TabsList>
                ) : null}

                {supported.includes('oauth') ? (
                  <TabsContent value="oauth" className="pt-4">
                    <ActionTabContent
                      notice={oauthNotice}
                      buttonLabel={t('connectWithOAuth')}
                      submitting={submitting}
                      onAction={() => void handleOAuth()}
                    />
                  </TabsContent>
                ) : null}

                {supported.includes('static') && connector.static_form_fields ? (
                  <TabsContent value="static" className="pt-4">
                    <MCPStaticForm
                      fields={connector.static_form_fields}
                      onSubmit={handleStaticSubmit}
                      submitting={submitting}
                    />
                  </TabsContent>
                ) : null}

                {supported.includes('none') ? (
                  <TabsContent value="none" className="pt-4">
                    <ActionTabContent
                      notice={t('noneNotice')}
                      buttonLabel={t('installButton')}
                      submitting={submitting}
                      onAction={() => void handleNoneInstall()}
                    />
                  </TabsContent>
                ) : null}
              </Tabs>
            </div>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
