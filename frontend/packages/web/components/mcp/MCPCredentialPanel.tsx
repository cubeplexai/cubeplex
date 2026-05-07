'use client'

import { useEffect, useState } from 'react'
import type { ApiClient, MCPServer } from '@cubebox/core'
import {
  wsDeleteMyCredential,
  wsDeleteWorkspaceCredential,
  wsGetMyCredential,
  wsGetWorkspaceCredential,
  wsPutMyCredential,
  wsPutWorkspaceCredential,
} from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

import { MCPSecretInput } from './MCPSecretInput'

export interface MCPCredentialPanelProps {
  server: MCPServer
  wsId: string
  client: ApiClient
  scopeContext: 'owned' | 'via-binding'
  onChange?: () => void
}

export function MCPCredentialPanel({
  server,
  wsId,
  client,
  scopeContext,
  onChange,
}: MCPCredentialPanelProps) {
  const t = useTranslations('mcp.credential')
  const tSecret = useTranslations('mcp.secret')
  const [hasValue, setHasValue] = useState(false)
  const [draftPlain, setDraftPlain] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isUserScope = server.credential_scope === 'user'
  const isWorkspaceScope = server.credential_scope === 'workspace'
  const isOrgScope = server.credential_scope === 'org'
  const isNoneScope = server.credential_scope === 'none'

  useEffect(() => {
    let active = true

    async function loadStatus(): Promise<void> {
      if (!isUserScope && !isWorkspaceScope) return
      setLoading(true)
      setError(null)
      try {
        const status = isUserScope
          ? await wsGetMyCredential(client, wsId, server.id)
          : await wsGetWorkspaceCredential(client, wsId, server.id)
        if (active) setHasValue(status.has_value)
      } catch (err) {
        if (active) setError((err as Error).message)
      } finally {
        if (active) setLoading(false)
      }
    }

    void loadStatus()
    return () => {
      active = false
    }
  }, [client, isUserScope, isWorkspaceScope, server.id, wsId])

  if (isOrgScope) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('managedTitle')}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{t('managedBody')}</p>
        </CardContent>
      </Card>
    )
  }

  if (isNoneScope) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('authTitle')}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{t('passthroughBody')}</p>
        </CardContent>
      </Card>
    )
  }

  const title = isUserScope ? t('myCredential') : t('workspaceCredential')
  const missingCopy = isUserScope
    ? t('missingUser')
    : scopeContext === 'via-binding'
      ? t('missingViaBinding')
      : t('missingWorkspace')

  async function save(): Promise<void> {
    setError(null)
    if (isUserScope) {
      await wsPutMyCredential(client, wsId, server.id, { plaintext: draftPlain })
    } else {
      await wsPutWorkspaceCredential(client, wsId, server.id, { plaintext: draftPlain })
    }
    setHasValue(true)
    setDraftPlain('')
    onChange?.()
  }

  async function clear(): Promise<void> {
    setError(null)
    if (isUserScope) {
      await wsDeleteMyCredential(client, wsId, server.id)
    } else {
      await wsDeleteWorkspaceCredential(client, wsId, server.id)
    }
    setHasValue(false)
    setDraftPlain('')
    onChange?.()
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {loading ? <p className="text-sm text-muted-foreground">{t('loadingStatus')}</p> : null}
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <MCPSecretInput
          label={tSecret('apiKeyPlaceholder')}
          hasValue={hasValue}
          required={!hasValue}
          onChange={setDraftPlain}
        />
        <div className="flex gap-2">
          <Button type="button" onClick={save} disabled={!draftPlain}>
            {t('save')}
          </Button>
          {hasValue ? (
            <Button type="button" variant="outline" onClick={clear}>
              {t('clear')}
            </Button>
          ) : null}
        </div>
        {!hasValue ? <p className="text-xs text-muted-foreground">{missingCopy}</p> : null}
      </CardContent>
    </Card>
  )
}
