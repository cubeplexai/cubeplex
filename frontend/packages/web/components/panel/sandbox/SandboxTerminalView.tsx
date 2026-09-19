'use client'

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useSandboxTerminal } from '@/hooks/useSandboxTerminal'
import { csrfHeaders } from '@/lib/csrf'
import { cn } from '@/lib/utils'

interface RunningCommand {
  id: string
  description: string
  status: string
  started_at: string
  kind: string
  lifetime: string
}

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

  let terminal: React.ReactNode = null
  if (loading) {
    terminal = (
      <div
        className="flex h-full items-center justify-center
          bg-black text-sm text-white/60"
      >
        Starting terminal…
      </div>
    )
  } else if (error) {
    terminal = (
      <div
        className="flex h-full flex-col items-center
          justify-center gap-3 text-sm"
      >
        <p className="text-destructive">Could not start terminal. {error.message}</p>
        <Button type="button" onClick={() => refresh()} variant="outline" size="sm">
          <RefreshCw className="size-3" />
          Retry
        </Button>
      </div>
    )
  } else if (url) {
    terminal = <TerminalFrame url={url} />
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {conversationId ? (
        <RunningCommandList workspaceId={workspaceId} conversationId={conversationId} />
      ) : null}
      <div className="min-h-0 flex-1">{terminal}</div>
    </div>
  )
}

function RunningCommandList({
  workspaceId,
  conversationId,
}: {
  workspaceId: string
  conversationId: string
}) {
  const [rows, setRows] = useState<RunningCommand[]>([])
  const [killing, setKilling] = useState<string | null>(null)
  const load = useCallback(async () => {
    const res = await fetch(
      `/api/v1/ws/${workspaceId}/conversations/${conversationId}/sandbox-commands`,
      { credentials: 'include' },
    )
    if (!res.ok) return
    const data: unknown = await res.json()
    if (Array.isArray(data)) setRows(data as RunningCommand[])
  }, [workspaceId, conversationId])

  useEffect(() => {
    const initial = setTimeout(() => void load(), 0)
    const id = setInterval(() => void load(), 5000)
    return () => {
      clearTimeout(initial)
      clearInterval(id)
    }
  }, [load])

  const kill = async (commandId: string) => {
    setKilling(commandId)
    try {
      const res = await fetch(
        `/api/v1/ws/${workspaceId}/conversations/${conversationId}/sandbox-commands/${commandId}/kill`,
        { method: 'POST', credentials: 'include', headers: csrfHeaders() },
      )
      if (res.ok) await load()
    } finally {
      setKilling(null)
    }
  }

  if (rows.length === 0) return null
  return (
    <ul className="border-b border-border bg-muted/40 px-3 py-2 text-xs">
      {rows.map((row) => (
        <li key={row.id} className="flex items-center justify-between gap-2 py-0.5">
          <span className="min-w-0 truncate">
            {row.description || row.id}
            <span className="ml-1 text-muted-foreground">· {formatElapsed(row.started_at)}</span>
          </span>
          <Button
            type="button"
            className="shrink-0"
            variant="destructive"
            size="xs"
            aria-label={`Kill ${row.description || row.id}`}
            disabled={killing === row.id}
            onClick={() => void kill(row.id)}
          >
            {killing === row.id ? 'Killing…' : 'Kill'}
          </Button>
        </li>
      ))}
    </ul>
  )
}

function formatElapsed(startedAt: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(startedAt)) / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}
