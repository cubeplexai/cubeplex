'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Eye, Hand, RefreshCw } from 'lucide-react'
import { usePanelStore } from '@cubebox/core'

import { PanelHeader } from '@/components/panel/PanelHeader'
import { useBrowserLiveView } from '@/hooks/useBrowserLiveView'
import { csrfHeaders } from '@/lib/csrf'

// Keep a long takeover session — whose traffic goes straight to Neko — from
// being TTL-reaped. The backend keepalive force-updates activity (bypasses the
// touch throttle), so every ping reliably extends the TTL.
const KEEPALIVE_MS = 30_000

interface BrowserViewProps {
  workspaceId: string | null
  /** Only fetch/connect when the live view is actually needed. */
  enabled?: boolean
  /** Hide the PanelHeader when embedded in another panel (e.g. SandboxPanel). */
  hideHeader?: boolean
  /** Expose the refresh function to a parent component. */
  refreshRef?: React.MutableRefObject<(() => void) | null>
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
export function BrowserView({
  workspaceId,
  enabled = true,
  hideHeader,
  refreshRef,
}: BrowserViewProps) {
  const { url, loading, error, refresh } = useBrowserLiveView(workspaceId, enabled)
  const close = usePanelStore((s) => s.close)
  const [takeover, setTakeover] = useState(false)
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (refreshRef) refreshRef.current = () => refresh()
    return () => {
      if (refreshRef) refreshRef.current = null
    }
  }, [refreshRef, refresh])

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

  const takeoverButton = (
    <button
      type="button"
      onClick={() => setTakeover((v) => !v)}
      className="rounded bg-primary px-2.5 py-0.5 text-xs font-medium text-primary-foreground hover:opacity-90 transition-opacity duration-fast"
    >
      {takeover ? 'Hand back to agent' : 'Take over'}
    </button>
  )

  const actionButtons = (
    <>
      <button
        type="button"
        onClick={() => refresh()}
        className="p-1 rounded-xs text-muted-foreground hover:bg-accent transition-colors duration-fast"
        aria-label="Refresh live view"
      >
        <RefreshCw className="size-3.5" />
      </button>
      {takeoverButton}
    </>
  )

  return (
    <div className="flex h-full w-full flex-col">
      {hideHeader ? (
        <div className="flex items-center gap-2 border-b border-border bg-card px-3 py-1.5 shrink-0">
          {takeover ? (
            <Hand className="size-3.5 text-warning-fg shrink-0" />
          ) : (
            <Eye className="size-3.5 text-muted-foreground shrink-0" />
          )}
          <span className="text-xs font-medium text-muted-foreground">
            {takeover ? 'You are in control' : 'Watching agent'}
          </span>
          <span className="flex-1" />
          {takeoverButton}
        </div>
      ) : (
        <PanelHeader
          source={{
            kind: 'plain',
            icon: takeover ? (
              <Hand className="size-3.5 text-warning-fg shrink-0" />
            ) : (
              <Eye className="size-3.5 text-muted-foreground shrink-0" />
            ),
            title: takeover ? 'You are in control' : 'Watching agent',
          }}
          actions={actionButtons}
          onClose={close}
        />
      )}

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
              // fullscreen: a cross-origin iframe can't enter fullscreen without
              // this delegation, so Neko's fullscreen button is otherwise a no-op.
              allow="fullscreen; clipboard-read; clipboard-write"
              allowFullScreen
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
