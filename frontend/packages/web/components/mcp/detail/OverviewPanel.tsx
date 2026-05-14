'use client'

import type { ApiClient, MCPServer } from '@cubebox/core'
import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'

import { MCPCredentialPanel } from '../MCPCredentialPanel'
import { MCPScopeBadge } from '../MCPScopeBadge'

export interface OverviewPanelProps {
  server: MCPServer
  mode: 'admin' | 'ws-owned' | 'ws-readonly'
  client: ApiClient
  wsId?: string
  onRefresh: () => Promise<void>
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  async function handleCopy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      // clipboard unavailable
    }
  }
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={() => void handleCopy()}
      className="h-7 w-7 p-0"
      aria-label="copy"
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </Button>
  )
}

function Row({
  label,
  children,
  mono,
  copyValue,
}: {
  label: string
  children: React.ReactNode
  mono?: boolean
  copyValue?: string
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border/40 px-4 py-2.5 text-sm last:border-b-0">
      <span className="text-muted-foreground">{label}</span>
      <div className="flex min-w-0 items-center gap-2">
        <span className={cn('truncate text-right', mono ? 'font-mono' : 'font-medium')}>
          {children}
        </span>
        {copyValue ? <CopyButton value={copyValue} /> : null}
      </div>
    </div>
  )
}

export function OverviewPanel({ server, mode, client, wsId, onRefresh }: OverviewPanelProps) {
  const t = useTranslations('mcp.detail.connectionCard')
  const showCredentialPanel = (mode === 'ws-owned' || mode === 'ws-readonly') && wsId

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>{t('title')}</CardTitle>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          <Row label={t('url')} mono copyValue={server.server_url}>
            {server.server_url}
          </Row>
          <Row label={t('transport')}>
            <span className="rounded-md border border-border px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
              {server.transport}
            </span>
          </Row>
          <Row label={t('authMethod')} mono copyValue={server.auth_method}>
            {server.auth_method}
          </Row>
          <Row label={t('scope')}>
            <MCPScopeBadge scope={server.credential_scope} />
          </Row>
          <Row label={t('timeouts')}>
            {t('timeoutsValue', {
              timeout: server.timeout,
              sseTimeout: server.sse_read_timeout,
            })}
          </Row>
        </CardContent>
      </Card>

      {showCredentialPanel && wsId ? (
        <MCPCredentialPanel
          server={server}
          wsId={wsId}
          client={client}
          scopeContext={mode === 'ws-owned' ? 'owned' : 'via-binding'}
          onChange={onRefresh}
        />
      ) : null}
    </div>
  )
}
