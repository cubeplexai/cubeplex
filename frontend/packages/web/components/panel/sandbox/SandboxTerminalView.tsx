'use client'

import { useEffect } from 'react'
import { RefreshCw } from 'lucide-react'

import { useSandboxTerminal } from '@/hooks/useSandboxTerminal'
import { csrfHeaders } from '@/lib/csrf'

const KEEPALIVE_MS = 30_000

interface SandboxTerminalViewProps {
  workspaceId: string
}

export function SandboxTerminalView({ workspaceId }: SandboxTerminalViewProps) {
  const { url, loading, error, refresh } = useSandboxTerminal(workspaceId)

  useEffect(() => {
    if (!url) return
    const ping = () => {
      void fetch(`/api/v1/ws/${workspaceId}/browser/keepalive`, {
        method: 'POST',
        credentials: 'include',
        headers: csrfHeaders(),
      }).catch(() => {})
    }
    const id = setInterval(ping, KEEPALIVE_MS)
    return () => clearInterval(id)
  }, [workspaceId, url])

  if (loading) {
    return (
      <div
        className="flex h-full items-center justify-center
          text-sm text-muted-foreground"
      >
        Starting terminal…
      </div>
    )
  }

  if (error) {
    return (
      <div
        className="flex h-full flex-col items-center
          justify-center gap-3 text-sm"
      >
        <p className="text-destructive">Could not start terminal. {error.message}</p>
        <button
          type="button"
          onClick={() => refresh()}
          className="inline-flex items-center gap-1.5
            rounded border border-border px-3 py-1.5
            text-xs font-medium hover:bg-muted
            transition-colors"
        >
          <RefreshCw className="size-3" />
          Retry
        </button>
      </div>
    )
  }

  if (!url) return null

  return (
    <iframe
      title="Sandbox terminal"
      src={url}
      className="h-full w-full border-0"
      allow="fullscreen; clipboard-read; clipboard-write"
    />
  )
}
