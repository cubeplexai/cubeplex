'use client'

import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'

import { useSandboxTerminal } from '@/hooks/useSandboxTerminal'
import { csrfHeaders } from '@/lib/csrf'
import { cn } from '@/lib/utils'

const KEEPALIVE_MS = 30_000

interface SandboxTerminalViewProps {
  workspaceId: string
  conversationId?: string | null
  refreshRef?: React.MutableRefObject<(() => Promise<unknown>) | null>
}

function TerminalFrame({ url }: { url: string }) {
  const [loaded, setLoaded] = useState(false)

  return (
    <div className="relative h-full w-full overflow-hidden bg-black">
      {!loaded && (
        <div
          className="absolute inset-0 z-10 flex items-center justify-center
            bg-black text-sm text-white/60"
        >
          Starting terminal…
        </div>
      )}
      <iframe
        title="Sandbox terminal"
        src={url}
        className={cn('h-full w-full border-0', !loaded && 'opacity-0')}
        allow="fullscreen; clipboard-read; clipboard-write"
        onLoad={() => setLoaded(true)}
      />
    </div>
  )
}

export function SandboxTerminalView({
  workspaceId,
  conversationId,
  refreshRef,
}: SandboxTerminalViewProps) {
  const { url, loading, error, refresh } = useSandboxTerminal(workspaceId, true, conversationId)

  useEffect(() => {
    if (refreshRef) refreshRef.current = () => refresh()
    return () => {
      if (refreshRef) refreshRef.current = null
    }
  }, [refreshRef, refresh])

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
          bg-black text-sm text-white/60"
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

  return <TerminalFrame url={url} />
}
