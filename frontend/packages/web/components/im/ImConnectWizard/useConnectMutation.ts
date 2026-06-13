'use client'

import { useState } from 'react'

import {
  ApiError,
  createApiClient,
  wsConnectImAccount,
  type ConnectFeishuAccountIn,
  type ImAccount,
} from '@cubebox/core'

// ``ApiClient`` is not re-exported from core's package index — using
// ``ReturnType<typeof createApiClient>`` keeps the type in sync with
// the actual factory without depending on the package's bundled ``dist/``
// layout (which a downstream consumer should never reach into).
type ApiClient = ReturnType<typeof createApiClient>

export type ConnectError = {
  shape: 'field' | 'banner' | 'toast'
  field?: string
  messageKey: string
  logId?: string
}

/**
 * Map a backend HTTP status + body into the 3-shape UI taxonomy from
 * spec §6. Pure function so it's trivially unit-testable.
 */
export function classifyConnectError(status: number, body: unknown): ConnectError {
  if (status === 0) return { shape: 'toast', messageKey: 'im.error.toast.network' }
  if (status === 409) return { shape: 'banner', messageKey: 'im.error.banner.duplicateApp' }
  if (status === 502) return { shape: 'banner', messageKey: 'im.error.banner.hydrationFailed' }
  if (status === 422) {
    const detail = (body as { detail?: Array<{ loc?: string[] }> } | null)?.detail
    const loc = Array.isArray(detail) && detail[0]?.loc
    const field = Array.isArray(loc) ? loc[loc.length - 1] : undefined
    return { shape: 'field', field, messageKey: 'im.error.field.appIdFormat' }
  }
  if (status === 400) {
    return { shape: 'field', field: 'app_secret', messageKey: 'im.error.field.appSecretBad' }
  }
  return { shape: 'banner', messageKey: 'im.error.banner.unknown' }
}

export function useConnectMutation(client: ApiClient, wsId: string) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ConnectError | null>(null)
  const [result, setResult] = useState<ImAccount | null>(null)

  async function submit(body: ConnectFeishuAccountIn): Promise<ImAccount | null> {
    setBusy(true)
    setError(null)
    try {
      const out = await wsConnectImAccount(client, wsId, body)
      setResult(out)
      return out
    } catch (e: unknown) {
      const status =
        e instanceof ApiError
          ? e.status
          : typeof (e as { status?: number })?.status === 'number'
            ? (e as { status: number }).status
            : 0
      const errBody =
        e instanceof ApiError ? { detail: e.detail } : ((e as { body?: unknown })?.body ?? null)
      const c = classifyConnectError(status, errBody)
      setError(c)
      return null
    } finally {
      setBusy(false)
    }
  }

  return { submit, busy, error, result }
}
