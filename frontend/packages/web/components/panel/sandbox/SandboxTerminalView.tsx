'use client'

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { createApiClient, listBackgroundTasks, stopBackgroundTask } from '@cubeplex/core'
import type { BackgroundTask } from '@cubeplex/core'

import { Button } from '@/components/ui/button'
import { useSandboxTerminal } from '@/hooks/useSandboxTerminal'
import { csrfHeaders } from '@/lib/csrf'
import { cn } from '@/lib/utils'

const KEEPALIVE_MS = 30_000

interface LegacyCommand {
  id: string
  description: string
  started_at: string
}

type RunningWork =
  { source: 'task'; task: BackgroundTask } | { source: 'legacy'; command: LegacyCommand }

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
  const [rows, setRows] = useState<RunningWork[]>([])
  const [stopping, setStopping] = useState<string | null>(null)
  const load = useCallback(async () => {
    const client = createApiClient('')
    client.setWorkspaceId(workspaceId)
    const next: RunningWork[] = []
    let loaded = false
    try {
      const tasks = await listBackgroundTasks(client, conversationId)
      next.push(...tasks.map((task) => ({ source: 'task' as const, task })))
      loaded = true
    } catch {
      // Keep the last durable snapshot visible through a transient refresh failure.
    }
    try {
      const response = await fetch(
        `/api/v1/ws/${workspaceId}/conversations/${conversationId}/sandbox-commands`,
        { credentials: 'include' },
      )
      if (response.ok) {
        const body: unknown = await response.json()
        const legacy = Array.isArray(body) ? (body as LegacyCommand[]) : []
        next.push(...legacy.map((command) => ({ source: 'legacy' as const, command })))
        loaded = true
      }
    } catch {
      // The migration-only endpoint disappears after cutover; managed tasks stay available.
    }
    if (loaded) setRows(next)
  }, [workspaceId, conversationId])

  useEffect(() => {
    const initial = setTimeout(() => void load(), 0)
    const id = setInterval(() => void load(), 5000)
    return () => {
      clearTimeout(initial)
      clearInterval(id)
    }
  }, [load])

  const stop = async (row: RunningWork) => {
    const id = row.source === 'task' ? row.task.id : row.command.id
    setStopping(id)
    try {
      if (row.source === 'task') {
        const client = createApiClient('')
        client.setWorkspaceId(workspaceId)
        await stopBackgroundTask(client, conversationId, row.task.id)
      } else {
        const response = await fetch(
          `/api/v1/ws/${workspaceId}/conversations/${conversationId}` +
            `/sandbox-commands/${row.command.id}/kill`,
          { method: 'POST', credentials: 'include', headers: csrfHeaders() },
        )
        if (!response.ok) return
      }
      await load()
    } finally {
      setStopping(null)
    }
  }

  if (rows.length === 0) return null
  return (
    <ul className="border-b border-border bg-muted/40 px-3 py-2 text-xs">
      {rows.map((row) => {
        const item = row.source === 'task' ? row.task : row.command
        const stopRequested = row.source === 'task' && row.task.stop_requested_at !== null
        return (
          <li
            key={`${row.source}:${item.id}`}
            className="flex items-center justify-between gap-2 py-0.5"
          >
            <span className="min-w-0 truncate">
              {item.description || item.id}
              <span className="ml-1 text-muted-foreground">
                ·{' '}
                {formatElapsed(
                  row.source === 'task' ? row.task.created_at : row.command.started_at,
                )}
              </span>
            </span>
            <Button
              type="button"
              className="shrink-0"
              variant="destructive"
              size="xs"
              aria-label={`Stop ${item.description || item.id}`}
              disabled={stopping === item.id || stopRequested}
              onClick={() => void stop(row)}
            >
              {stopping === item.id || stopRequested ? 'Stopping…' : 'Stop'}
            </Button>
          </li>
        )
      })}
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
