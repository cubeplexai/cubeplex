'use client'

import { useCallback, useState } from 'react'
import { csrfHeaders, readApiError } from '@/lib/csrf'

export interface PublishResult {
  ok: boolean
  message: string
}

export function usePublishSkill(workspaceId: string, artifactId: string) {
  const [isPublishing, setIsPublishing] = useState(false)
  const [result, setResult] = useState<PublishResult | null>(null)

  const publish = useCallback(async () => {
    setIsPublishing(true)
    setResult(null)
    try {
      const res = await fetch(`/api/v1/ws/${workspaceId}/skills/publish`, {
        method: 'POST',
        credentials: 'include',
        headers: { ...csrfHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ artifact_id: artifactId }),
      })
      if (res.status === 409) {
        setResult({ ok: false, message: 'VERSION_EXISTS' })
        return
      }
      if (!res.ok) {
        setResult({ ok: false, message: await readApiError(res) })
        return
      }
      setResult({ ok: true, message: 'SUCCESS' })
    } finally {
      setIsPublishing(false)
    }
  }, [workspaceId, artifactId])

  const reset = useCallback(() => setResult(null), [])

  return { publish, isPublishing, result, reset }
}
