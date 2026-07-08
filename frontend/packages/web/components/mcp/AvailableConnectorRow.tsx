'use client'

import * as React from 'react'
import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Loader2 } from 'lucide-react'
import {
  wsCreateInstall,
  wsPatchConnectorState,
  type ApiClient,
  type MCPAuthMethod,
  type MCPCredentialScope,
  type WsAvailable,
} from '@cubebox/core'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

export interface AvailableConnectorRowProps {
  row: WsAvailable
  client: ApiClient
  wsId: string
  /**
   * Called after the row moves to Installed. The argument is the
   * install id the row now points at — the org install's id when the
   * source was `org_install` (just had its state row enabled), or the
   * freshly-created workspace install's id when the source was
   * `template`. The parent uses it to select the row so the detail
   * panel + auth band open automatically and the operator can finish
   * credential provisioning without hunting for the new install.
   */
  onConnected: (connectorId: string) => Promise<void>
}

export function AvailableConnectorRow({
  row,
  client,
  wsId,
  onConnected,
}: AvailableConnectorRowProps): React.ReactElement {
  const t = useTranslations('mcp.available')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const name = row.install?.name ?? row.template?.name ?? '—'
  const description = row.template?.description ?? ''
  const provider = row.template?.provider ?? ''

  async function handleConnect(): Promise<void> {
    setBusy(true)
    setError(null)
    try {
      let connectorId: string | null = null
      if (row.source === 'org_install' && row.install) {
        await wsPatchConnectorState(client, wsId, row.install.connector_id, {
          enabled: true,
          credential_policy: 'workspace',
        })
        connectorId = row.install.connector_id
      } else if (row.source === 'template' && row.template) {
        const tpl = row.template
        const method: MCPAuthMethod =
          tpl.supported_auth_methods.find((m) => m === 'oauth') ??
          tpl.supported_auth_methods.find((m) => m === 'static') ??
          tpl.supported_auth_methods[0]
        const policy: MCPCredentialScope = method === 'none' ? 'none' : 'workspace'
        const created = await wsCreateInstall(client, wsId, {
          template_id: tpl.template_id,
          install_scope: 'workspace',
          auth_method: method,
          default_credential_policy: policy,
        })
        connectorId = created.connector_id
      }
      if (connectorId !== null) {
        await onConnected(connectorId)
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="flex items-center justify-between gap-2 rounded-lg border border-border/70 bg-card/40 p-3"
      data-testid={`ws-available-row-${row.install?.connector_id ?? row.template?.template_id ?? 'unknown'}`}
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold">{name}</span>
          {provider && provider.toLowerCase() !== name.toLowerCase() ? (
            <Badge variant="outline" className="text-[10px]">
              {provider}
            </Badge>
          ) : null}
        </div>
        {description ? (
          <p className="line-clamp-1 text-xs text-muted-foreground">{description}</p>
        ) : null}
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={() => void handleConnect()}
      >
        {busy ? <Loader2 className="mr-2 size-3.5 animate-spin" /> : null}
        {t('connect')}
      </Button>
    </div>
  )
}
