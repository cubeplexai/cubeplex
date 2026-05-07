'use client'

import { useMemo } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { createApiClient, useWorkspaceMcpStore } from '@cubebox/core'
import type { MCPServerCreateWSBody } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { MCPServerForm, type MCPServerFormValues } from '@/components/mcp/MCPServerForm'

export default function NewWorkspaceMcpPage() {
  const t = useTranslations('mcp.wsPage')
  const { wsId } = useParams<{ wsId: string }>()
  const router = useRouter()
  const client = useMemo(() => {
    const next = createApiClient('')
    next.setWorkspaceId(wsId)
    return next
  }, [wsId])
  const { create, testConnection } = useWorkspaceMcpStore()

  function toWorkspaceBody(values: MCPServerFormValues): MCPServerCreateWSBody {
    if (
      values.credential_scope !== 'workspace' &&
      values.credential_scope !== 'user' &&
      values.credential_scope !== 'none'
    ) {
      throw new Error(t('scopeError'))
    }

    return {
      name: values.name,
      server_url: values.server_url,
      transport: values.transport,
      auth_method: values.auth_method,
      credential_scope: values.credential_scope,
      credential_plaintext: values.credential_plaintext || undefined,
      credential_name: values.credential_name || undefined,
      headers: values.headers,
      timeout: values.timeout,
      sse_read_timeout: values.sse_read_timeout,
    }
  }

  async function handleSubmit(values: MCPServerFormValues): Promise<void> {
    const created = await create(client, wsId, toWorkspaceBody(values))
    router.push(`/w/${wsId}/integrations/mcp/${created.id}`)
  }

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">{t('addTitle')}</h1>
        <p className="text-sm text-muted-foreground">{t('addSubtitle')}</p>
      </div>
      <MCPServerForm
        mode="ws-member"
        onSubmit={handleSubmit}
        onTestConnection={(values) =>
          testConnection(client, wsId, {
            server_url: values.server_url,
            transport: values.transport,
            auth_method: values.auth_method,
            credential_scope: values.credential_scope,
            credential_plaintext: values.credential_plaintext || undefined,
            headers: values.headers,
            timeout: values.timeout,
            sse_read_timeout: values.sse_read_timeout,
          })
        }
        onCancel={() => router.back()}
      />
    </div>
  )
}
