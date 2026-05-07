'use client'

import { useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, useMcpStore } from '@cubebox/core'
import type { MCPServerCreateAdminBody } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { MCPServerForm, type MCPServerFormValues } from '@/components/mcp/MCPServerForm'

export default function NewAdminMcpPage() {
  const t = useTranslations('mcp.adminPage')
  const router = useRouter()
  const client = useMemo(() => createApiClient(''), [])
  const { create, testConnection } = useMcpStore()

  function toAdminBody(values: MCPServerFormValues): MCPServerCreateAdminBody {
    if (
      values.credential_scope !== 'org' &&
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
    const created = await create(client, toAdminBody(values))
    router.push(`/admin/mcp/${created.id}`)
  }

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">{t('addTitle')}</h1>
        <p className="text-sm text-muted-foreground">{t('addSubtitle')}</p>
      </div>
      <MCPServerForm
        mode="admin"
        onSubmit={handleSubmit}
        onTestConnection={(values) =>
          testConnection(client, {
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
