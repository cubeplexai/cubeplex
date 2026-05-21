'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Eye, Hand, RefreshCw } from 'lucide-react'

import { useBrowserLiveView } from '@/hooks/useBrowserLiveView'
import { csrfHeaders } from '@/lib/csrf'

// Ping the backend below the sandbox touch_interval (default 60s) so a long
// takeover session — whose traffic goes straight to Neko — isn't TTL-reaped.
const KEEPALIVE_MS = 45_000

interface BrowserViewProps {
  workspaceId: string | null
  /** Only fetch/connect when the live view is actually needed. */
  enabled?: boolean
}

/**
 * Live view of the sandbox browser (Neko, embedded via iframe).
 *
 * Two modes:
 * - watch-only (default): a true input lock — a transparent overlay swallows
 *   pointer AND keyboard events and the iframe is `inert`/non-focusable — so the
 *   user cannot disrupt the agent while it drives.
 * - takeover: the lock is lifted and the user can click/type (login, OAuth, …).
 */
export function BrowserView({ workspaceId, enabled = true }: BrowserViewProps) {
  const { url, loading, error, refresh } = useBrowserLiveView(workspaceId, enabled)
  const [takeover, setTakeover] = useState(false)
  const overlayRef = useRef<HTMLDivElement>(null)

  // While watch-only, swallow any keyboard event that reaches the overlay.
  const swallow = useCallback((e: React.SyntheticEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  // Keep the sandbox alive while the live view is open (iframe traffic bypasses
  // the backend, so without this a long takeover could be TTL-reaped).
  useEffect(() => {
    if (!workspaceId || !url) return
    const ping = () => {
      // Must carry the CSRF token or CSRFMiddleware rejects the authenticated POST.
      void fetch(`/api/v1/ws/${workspaceId}/browser/keepalive`, {
        method: 'POST',
        credentials: 'include',
        headers: csrfHeaders(),
      }).catch(() => {})
    }
    const id = setInterval(ping, KEEPALIVE_MS)
    return () => clearInterval(id)
  }, [workspaceId, url])

  if (!workspaceId) return null

  return (
    <div className="flex h-full w-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          {takeover ? (
            <Hand className="h-4 w-4 text-amber-500" />
          ) : (
            <Eye className="h-4 w-4 text-muted-foreground" />
          )}
          <span>{takeover ? 'You are in control' : 'Watching agent'}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => refresh()}
            className="rounded p-1 text-muted-foreground hover:bg-accent"
            aria-label="Refresh live view"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setTakeover((v) => !v)}
            className="rounded-md bg-primary px-3 py-1 text-xs font-medium text-primary-foreground hover:opacity-90"
          >
            {takeover ? 'Hand back to agent' : 'Take over'}
          </button>
        </div>
      </div>

      <div className="relative flex-1 overflow-hidden">
        {loading && (
          <div className="absolute inset-0 grid place-items-center text-sm text-muted-foreground">
            Connecting to the sandbox browser…
          </div>
        )}
        {error && (
          <div className="absolute inset-0 grid place-items-center px-4 text-center text-sm text-destructive">
            Could not open the sandbox browser. {error.message}
          </div>
        )}
        {url && (
          <>
            <iframe
              title="Sandbox browser"
              src={url}
              className="h-full w-full border-0"
              // When watch-only, make the frame non-focusable so keyboard can't
              // reach it. `inert` removes it from the focus/interaction tree.
              inert={!takeover}
              tabIndex={takeover ? undefined : -1}
              allow="clipboard-read; clipboard-write"
            />
            {!takeover && (
              // Transparent input lock: captures pointer + keyboard so neither
              // reaches the iframe while the agent is in control.
              <div
                ref={overlayRef}
                className="absolute inset-0 z-10 cursor-not-allowed"
                tabIndex={0}
                role="presentation"
                onKeyDown={swallow}
                onKeyUp={swallow}
                onKeyPress={swallow}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}
