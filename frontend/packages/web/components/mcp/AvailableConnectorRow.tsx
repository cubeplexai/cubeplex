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
  onConnected: () => Promise<void>
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
      if (row.source === 'org_install' && row.install) {
        await wsPatchConnectorState(client, wsId, row.install.install_id, {
          enabled: true,
        })
      } else if (row.source === 'template' && row.template) {
        const tpl = row.template
        const method: MCPAuthMethod =
          tpl.supported_auth_methods.find((m) => m === 'oauth') ??
          tpl.supported_auth_methods.find((m) => m === 'static') ??
          tpl.supported_auth_methods[0]
        const policy: MCPCredentialScope =
          method === 'none'
            ? 'none'
            : tpl.default_credential_policy === 'none'
              ? 'user'
              : tpl.default_credential_policy
        await wsCreateInstall(client, wsId, {
          template_id: tpl.template_id,
          install_scope: 'workspace',
          auth_method: method,
          default_credential_policy: policy,
        })
      }
      await onConnected()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="flex items-center justify-between gap-2 rounded-lg border border-border/70 bg-card/40 p-3"
      data-testid={`ws-available-row-${row.install?.install_id ?? row.template?.template_id ?? 'unknown'}`}
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold">{name}</span>
          {provider ? (
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
      <Button type="button" size="sm" disabled={busy} onClick={() => void handleConnect()}>
        {busy ? <Loader2 className="mr-2 size-3.5 animate-spin" /> : null}
        {t('connect')}
      </Button>
    </div>
  )
}
