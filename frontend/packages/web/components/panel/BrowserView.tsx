'use client'

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import { createPortal } from 'react-dom'
import { Eye, Hand, Maximize2, Minimize2, RefreshCw, X } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { usePanelStore } from '@cubeplex/core'

import { PanelHeader } from '@/components/panel/PanelHeader'
import { ArtifactExpandDialog } from '@/components/panel/artifact/ArtifactExpandDialog'
import { useBrowserLiveView } from '@/hooks/useBrowserLiveView'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { csrfHeaders } from '@/lib/csrf'
import { cn } from '@/lib/utils'

// Keep a long takeover session — whose traffic goes straight to Neko — from
// being TTL-reaped. The backend keepalive force-updates activity (bypasses the
// touch throttle), so every ping reliably extends the TTL.
const KEEPALIVE_MS = 30_000

/**
 * Live-view frame aspect. Matches sandbox `NEKO_DESKTOP_SCREEN` (1280×900).
 * Fitting the iframe host to this ratio (instead of stretching into a tall
 * rail) keeps the desktop from looking flat and maps clicks more naturally.
 */
const DESKTOP_WIDTH = 1280
const DESKTOP_HEIGHT = 900
const DESKTOP_ASPECT = DESKTOP_WIDTH / DESKTOP_HEIGHT

interface BrowserViewProps {
  workspaceId: string | null
  /** Only fetch/connect when the live view is actually needed. */
  enabled?: boolean
  /** Hide the PanelHeader when embedded in another panel (e.g. SandboxPanel). */
  hideHeader?: boolean
  /** Expose the refresh function to a parent component. */
  refreshRef?: React.MutableRefObject<(() => void) | null>
  /** Route live-view + keepalive to the conversation's shared sandbox (group chat / topic). */
  conversationId?: string | null
}

interface LiveFrameProps {
  url: string | null
  /** True while first load or SWR is retrying a transient failure. */
  showConnecting: boolean
  /** Terminal error after retries exhausted and still no URL. */
  showError: boolean
  error: Error | undefined
  takeover: boolean
  swallow: (e: React.SyntheticEvent) => void
  testId?: string
  /**
   * Bumped on explicit refresh so the iframe remounts even when the signed URL
   * string is unchanged (otherwise React keeps the dead WebRTC peer).
   */
  frameKey?: number
}

/** Largest desktop-aspect box that fits inside the parent. */
function useContainedSize(aspect: number): {
  ref: React.RefObject<HTMLDivElement | null>
  width: number
  height: number
} {
  const ref = useRef<HTMLDivElement | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = (w: number, h: number) => {
      if (w <= 0 || h <= 0) {
        setSize({ width: 0, height: 0 })
        return
      }
      let width = w
      let height = width / aspect
      if (height > h) {
        height = h
        width = height * aspect
      }
      setSize({ width: Math.round(width), height: Math.round(height) })
    }
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      const { width, height } = entry.contentRect
      measure(width, height)
    })
    ro.observe(el)
    // Initial paint before the first RO callback (some envs fire async only).
    // Do not run this after a sync RO callback has already measured, or a
    // zero clientWidth/clientHeight in jsdom would clobber the real size.
    if (el.clientWidth > 0 && el.clientHeight > 0) {
      measure(el.clientWidth, el.clientHeight)
    }
    return () => ro.disconnect()
  }, [aspect])

  return { ref, width: size.width, height: size.height }
}

/**
 * Single host for the Neko iframe + watch-only input lock.
 * Aspect-boxed so a tall rail does not stretch the desktop into a flat strip.
 */
function BrowserLiveFrame({
  url,
  showConnecting,
  showError,
  error,
  takeover,
  swallow,
  testId,
  frameKey = 0,
}: LiveFrameProps) {
  const { ref, width, height } = useContainedSize(DESKTOP_ASPECT)

  return (
    <div
      ref={ref}
      className="relative flex h-full min-h-0 w-full items-center justify-center overflow-hidden bg-black"
      data-testid={testId}
    >
      {showConnecting && (
        <div className="absolute inset-0 z-10 grid place-items-center text-sm text-muted-foreground">
          Connecting to the sandbox browser…
        </div>
      )}
      {showError && error && (
        <div className="absolute inset-0 z-10 grid place-items-center px-4 text-center text-sm text-destructive">
          Could not open the sandbox browser. {error.message}
        </div>
      )}
      {url && width > 0 && height > 0 && (
        <div className="relative shrink-0" style={{ width, height }}>
          <iframe
            key={frameKey}
            title="Sandbox browser"
            src={url}
            className="absolute inset-0 h-full w-full border-0"
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
              className="absolute inset-0 z-10 cursor-not-allowed"
              tabIndex={0}
              role="presentation"
              onKeyDown={swallow}
              onKeyUp={swallow}
              onKeyPress={swallow}
            />
          )}
        </div>
      )}
    </div>
  )
}

const subscribeNoop = (): (() => void) => () => {}

/** True only after client hydration (SSR snapshot is false). */
function useIsClient(): boolean {
  return useSyncExternalStore(
    subscribeNoop,
    () => true,
    () => false,
  )
}

/**
 * Imperative portal host for the live frame. React portals into this element;
 * we reparent the element between rail and theater so the iframe DOM (and Neko
 * WebRTC peer) is never destroyed on expand/exit.
 *
 * Created once on the client (not during SSR). Cleanup removes the node on unmount.
 */
function useLiveFrameHost(): HTMLDivElement | null {
  const isClient = useIsClient()
  const host = useMemo(() => {
    if (!isClient) return null
    const el = document.createElement('div')
    el.dataset.browserLiveHost = 'true'
    el.className = 'h-full w-full min-h-0'
    return el
  }, [isClient])

  useEffect(() => {
    return () => {
      host?.remove()
    }
  }, [host])

  return host
}

/**
 * Live view of the sandbox browser (Neko, embedded via iframe).
 *
 * Two modes:
 * - watch-only (default): a true input lock — a transparent overlay swallows
 *   pointer AND keyboard events and the iframe is `inert`/non-focusable — so the
 *   user cannot disrupt the agent while it drives.
 * - takeover: the lock is lifted and the user can click/type (login, OAuth, …).
 *
 * Expand uses ArtifactExpandDialog for focus trap / Esc / backdrop, but the
 * Neko iframe lives in a movable portal host so WebRTC is not torn down.
 */
export function BrowserView({
  workspaceId,
  enabled = true,
  hideHeader,
  refreshRef,
  conversationId,
}: BrowserViewProps) {
  const { url, loading, error, validating, refresh } = useBrowserLiveView(
    workspaceId,
    enabled,
    conversationId,
  )
  // Keep "Connecting…" up while the first request is in flight OR while SWR is
  // retrying a transient failure (cold-start 503 / brief proxy blip). Only show
  // the hard error after retries are exhausted and we still have no URL.
  const showConnecting = !url && (loading || (Boolean(error) && validating))
  const showError = Boolean(error) && !url && !validating
  const close = usePanelStore((s) => s.close)
  const t = useTranslations('panel.header')
  const [takeover, setTakeover] = useState(false)
  // Explicit refresh remounts the Neko iframe even when the signed URL is stable.
  const [frameKey, setFrameKey] = useState(0)
  const handleRefresh = useCallback(() => {
    setFrameKey((k) => k + 1)
    void refresh()
  }, [refresh])
  // User intent; AND with isDesktop so a narrow viewport never shows theater
  // (no setState-in-effect to clear).
  const [expandIntent, setExpandIntent] = useState(false)
  const expandButtonRef = useRef<HTMLButtonElement | null>(null)
  const exitExpandButtonRef = useRef<HTMLButtonElement | null>(null)
  const railSlotRef = useRef<HTMLDivElement | null>(null)
  const theaterSlotRef = useRef<HTMLDivElement | null>(null)
  const host = useLiveFrameHost()
  // Expand is desktop-only.
  const isDesktop = useMediaQuery('(min-width: 768px)', true)
  const expanded = expandIntent && isDesktop

  useEffect(() => {
    if (refreshRef) refreshRef.current = handleRefresh
    return () => {
      if (refreshRef) refreshRef.current = null
    }
  }, [refreshRef, handleRefresh])

  /** Move the portal host into `slot` if it is not already there. */
  const attachHost = useCallback(
    (slot: HTMLDivElement | null) => {
      if (!host || !slot || host.parentElement === slot) return
      slot.appendChild(host)
    },
    [host],
  )

  // Default: keep host in the rail. Theater attach happens via theater slot
  // callback ref (dialog content mounts in a portal, so a plain layout effect
  // can race and see a null theaterSlotRef).
  useLayoutEffect(() => {
    if (!expanded) attachHost(railSlotRef.current)
  }, [expanded, host, attachHost])

  const setRailSlot = useCallback(
    (el: HTMLDivElement | null) => {
      railSlotRef.current = el
      if (!expanded) attachHost(el)
    },
    [expanded, attachHost],
  )

  const setTheaterSlot = useCallback(
    (el: HTMLDivElement | null) => {
      theaterSlotRef.current = el
      if (expanded) attachHost(el)
    },
    [expanded, attachHost],
  )

  // While watch-only, swallow any keyboard event that reaches the overlay.
  const swallow = useCallback((e: React.SyntheticEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  // Keep the sandbox alive while the live view is open (iframe traffic bypasses
  // the backend, so without this a long takeover could be TTL-reaped).
  useEffect(() => {
    if (!workspaceId || !url) return
    const convQs = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
    const ping = () => {
      // Must carry the CSRF token or CSRFMiddleware rejects the authenticated POST.
      void fetch(`/api/v1/ws/${workspaceId}/browser/keepalive${convQs}`, {
        method: 'POST',
        credentials: 'include',
        headers: csrfHeaders(),
      }).catch(() => {})
    }
    const id = setInterval(ping, KEEPALIVE_MS)
    return () => clearInterval(id)
  }, [workspaceId, url, conversationId])

  if (!workspaceId) return null

  const openExpand = (): void => {
    if (isDesktop) setExpandIntent(true)
  }
  const closeExpand = (): void => {
    // Move host out of the dialog tree before React unmounts the dialog.
    const rail = railSlotRef.current
    if (host && rail && host.parentElement !== rail) {
      rail.appendChild(host)
    }
    setExpandIntent(false)
  }
  const handlePanelClose = (): void => {
    closeExpand()
    close()
  }

  const takeoverButton = (
    <button
      type="button"
      onClick={() => setTakeover((v) => !v)}
      className="rounded bg-primary px-2.5 py-0.5 text-xs font-medium text-primary-foreground hover:opacity-90 transition-opacity duration-fast"
    >
      {takeover ? 'Hand back to agent' : 'Take over'}
    </button>
  )

  const expandControl = (opts: {
    active: boolean
    onToggle: () => void
    buttonRef?: React.RefObject<HTMLButtonElement | null>
    className?: string
  }) => (
    <button
      type="button"
      ref={opts.buttonRef}
      onClick={opts.onToggle}
      className={cn(
        'hidden md:inline-flex p-1 rounded-xs text-muted-foreground hover:bg-accent transition-colors duration-fast',
        opts.className,
      )}
      title={t(opts.active ? 'exitExpand' : 'expand')}
      data-testid={opts.active ? 'panel-exit-expand' : 'panel-expand'}
      aria-label={t(opts.active ? 'exitExpand' : 'expand')}
    >
      {opts.active ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
    </button>
  )

  // Expand lives on PanelHeader's dedicated expand slot when the full header
  // is shown — only put refresh + takeover here so the control is not doubled.
  const actionButtons = (
    <>
      <button
        type="button"
        onClick={handleRefresh}
        className="p-1 rounded-xs text-muted-foreground hover:bg-accent transition-colors duration-fast"
        aria-label="Refresh live view"
      >
        <RefreshCw className="size-3.5" />
      </button>
      {takeoverButton}
    </>
  )

  const statusIcon = takeover ? (
    <Hand className="size-3.5 text-warning-fg shrink-0" />
  ) : (
    <Eye className="size-3.5 text-muted-foreground shrink-0" />
  )
  const statusTitle = takeover ? 'You are in control' : 'Watching agent'

  const livePortal =
    host &&
    createPortal(
      <BrowserLiveFrame
        url={url}
        showConnecting={showConnecting}
        showError={showError}
        error={error}
        takeover={takeover}
        swallow={swallow}
        testId="browser-live-preview"
        frameKey={frameKey}
      />,
      host,
    )

  return (
    <div className="flex h-full w-full flex-col">
      {hideHeader ? (
        <div className="flex items-center gap-2 border-b border-border bg-card px-3 py-1.5 shrink-0">
          {statusIcon}
          <span className="text-xs font-medium text-muted-foreground">{statusTitle}</span>
          <span className="flex-1" />
          {expandControl({
            active: expanded,
            onToggle: expanded ? closeExpand : openExpand,
            buttonRef: expandButtonRef,
          })}
          {takeoverButton}
        </div>
      ) : (
        <PanelHeader
          source={{
            kind: 'plain',
            icon: statusIcon,
            title: statusTitle,
          }}
          actions={actionButtons}
          expand={{
            active: expanded,
            onToggle: expanded ? closeExpand : openExpand,
          }}
          expandClassName="hidden md:inline-flex"
          expandButtonRef={expandButtonRef}
          onClose={handlePanelClose}
        />
      )}

      <div className="relative flex-1 min-h-0 overflow-hidden">
        <div
          ref={setRailSlot}
          className="h-full w-full min-h-0"
          data-testid={expanded ? 'browser-rail-placeholder' : 'browser-rail'}
        />
        {expanded && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center px-4 text-center text-sm text-muted-foreground">
            Expanded in preview
          </div>
        )}
      </div>

      {livePortal}

      {expanded && (
        <ArtifactExpandDialog
          open
          onOpenChange={(open) => {
            if (!open) closeExpand()
          }}
          title={statusTitle}
          identityKey={`browser:${workspaceId}:${conversationId ?? ''}`}
          initialFocusRef={exitExpandButtonRef}
          finalFocusRef={expandButtonRef}
          header={
            <div className="flex items-center gap-2 border-b border-border bg-card px-3 py-1.5 shrink-0">
              {statusIcon}
              <span className="text-xs font-medium text-muted-foreground">{statusTitle}</span>
              <span className="flex-1" />
              {expandControl({
                active: true,
                onToggle: closeExpand,
                buttonRef: exitExpandButtonRef,
                className: 'inline-flex',
              })}
              {takeoverButton}
              <button
                type="button"
                onClick={closeExpand}
                className="p-1 rounded-xs text-muted-foreground hover:bg-accent transition-colors duration-fast"
                title={t('close')}
                aria-label={t('close')}
              >
                <X className="size-3.5" />
              </button>
            </div>
          }
        >
          <div
            ref={setTheaterSlot}
            className="h-full w-full min-h-0"
            data-testid="browser-expand-preview"
          />
        </ArtifactExpandDialog>
      )}
    </div>
  )
}
