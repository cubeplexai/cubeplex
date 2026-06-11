'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Copy, Maximize2, Minimize2, RotateCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { WIDGET_SHELL_HTML } from './widgetShell'

const READY_TIMEOUT_MS = 5000
const MAX_HEIGHT_PX = 4000
const MAX_CODE_BYTES = 256 * 1024
const SKELETON_DEFAULT_HEIGHT = 240

interface ThemeTokens {
  bg: string
  fg: string
  muted: string
  border: string
  accent: string
}

/** Iframe srcdoc cannot inherit parent CSS variables, so we must inject
 *  literal hex into the widget shell. To avoid drift from the design
 *  tokens, derive from computed styles at serialization time; the SSR
 *  fallbacks here mirror the token palette (see globals.css §1). */
function resolveThemeTokens(isDark: boolean): ThemeTokens {
  const fallback: ThemeTokens = isDark
    ? { bg: '#050505', fg: '#e4e4e7', muted: '#1c1c1c', border: '#262626', accent: '#0070f3' }
    : { bg: '#fafafa', fg: '#171717', muted: '#f5f5f5', border: '#eaeaea', accent: '#0070f3' }
  if (typeof window === 'undefined' || typeof document === 'undefined') return fallback
  const styles = getComputedStyle(document.documentElement)
  const v = (name: string): string => styles.getPropertyValue(name).trim()
  return {
    bg: v('--color-sunken') || fallback.bg,
    fg: v('--color-foreground') || fallback.fg,
    muted: v('--color-raised') || fallback.muted,
    border: v('--color-border') || fallback.border,
    accent: v('--color-primary') || fallback.accent,
  }
}

function isAppDark(): boolean {
  return typeof document !== 'undefined' && document.documentElement.classList.contains('dark')
}

interface WidgetViewProps {
  widgetCode: string
  status: 'streaming' | 'complete'
  widgetId: string
  title?: string
  width?: number
  height?: number
}

// Focusable selector used for the fullscreen dialog's focus trap + initial
// focus. Includes the widget iframe so keyboard users can tab into the
// sandboxed content (e.g. sliders, interactive explainers). Sandbox is
// opaque-origin, so we can't inspect the child DOM from out here — but
// focusing the iframe element itself hands keyboard input to the iframe's
// browsing context, and the browser handles Tab navigation inside it from
// then on. Shift+Tab out of the first widget control returns to the dialog.
const FOCUSABLE_SELECTOR =
  'button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),' +
  'textarea:not([disabled]),iframe:not([tabindex="-1"]),' +
  '[tabindex]:not([tabindex="-1"])'

export function WidgetView(props: WidgetViewProps) {
  // The reloadKey is bumped by the toolbar Reload button (or the error-retry
  // button) to force-remount the iframe; the embedded WidgetFrame remounts
  // because its key changes, which throws away ready/failed/seq state.
  const [reloadKey, setReloadKey] = useState(0)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [copied, setCopied] = useState(false)
  const dialogRef = useRef<HTMLDivElement | null>(null)
  const prevFocusRef = useRef<HTMLElement | null>(null)

  const reload = useCallback(() => setReloadKey((k) => k + 1), [])

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(props.widgetCode)
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    } catch {
      /* clipboard blocked; silently no-op */
    }
  }, [props.widgetCode])

  // openFullscreen captures the currently-focused element SYNCHRONOUSLY before
  // it triggers the re-render that hides + inerts the inline copy. Capturing
  // it inside a post-render effect would be too late: by then the opener
  // button is inside an `inert` subtree, so document.activeElement has
  // already shifted to <body> and the restore-on-close would target the
  // wrong element.
  const openFullscreen = useCallback(() => {
    if (typeof document !== 'undefined') {
      prevFocusRef.current = (document.activeElement as HTMLElement | null) ?? null
    }
    setIsFullscreen(true)
  }, [])

  const closeFullscreen = useCallback(() => setIsFullscreen(false), [])

  // Esc closes fullscreen.
  useEffect(() => {
    if (!isFullscreen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeFullscreen()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isFullscreen, closeFullscreen])

  // After the dialog renders, move focus inside it. After it closes, restore
  // focus to whatever openFullscreen captured.
  useEffect(() => {
    if (!isFullscreen) return
    const raf = requestAnimationFrame(() => {
      const dialog = dialogRef.current
      if (!dialog) return
      const focusables = dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
      ;(focusables[0] ?? dialog).focus()
    })
    return () => {
      cancelAnimationFrame(raf)
      const prev = prevFocusRef.current
      prevFocusRef.current = null
      // Defer to next tick so React has finished unmounting the dialog before
      // we attempt to focus the (now visible again) opener button.
      setTimeout(() => prev?.focus?.(), 0)
    }
  }, [isFullscreen])

  // Tab trap: keep focus inside the dialog while it is open.
  const onDialogKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab') return
    const dialog = dialogRef.current
    if (!dialog) return
    const focusables = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    if (focusables.length === 0) return
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    const active = document.activeElement as HTMLElement | null
    if (e.shiftKey && (active === first || !dialog.contains(active))) {
      last.focus()
      e.preventDefault()
    } else if (!e.shiftKey && (active === last || !dialog.contains(active))) {
      first.focus()
      e.preventDefault()
    }
  }, [])

  return (
    <div className="relative group/widget">
      {/* Inline frame (also used as the layout-slot holder when fullscreen).
          Hidden visually while fullscreen so the host overlay is the only one
          rendering content, but the wrapping div keeps the message column
          height stable rather than collapsing as the iframe disappears.
          aria-hidden + inert hide the inline copy from assistive tech and
          tab order while the modal dialog is open. */}
      <div
        className={cn(
          'relative rounded-lg border border-border bg-muted overflow-hidden',
          isFullscreen && 'invisible',
        )}
        style={{
          width: props.width ? `${props.width}px` : '100%',
        }}
        inert={isFullscreen || undefined}
        aria-hidden={isFullscreen || undefined}
      >
        <WidgetFrame key={`inline-${reloadKey}`} {...props} onRetry={reload} />
        <WidgetToolbar
          copied={copied}
          onCopy={handleCopy}
          onReload={reload}
          onFullscreen={openFullscreen}
          isFullscreen={false}
        />
      </div>

      {isFullscreen &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            ref={dialogRef}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 md:p-8
              focus:outline-none"
            onClick={closeFullscreen}
            onKeyDown={onDialogKeyDown}
            role="dialog"
            aria-modal="true"
            aria-label={props.title ?? 'widget (fullscreen)'}
            tabIndex={-1}
          >
            <div
              className="relative w-full h-full max-w-[1200px] rounded-xl border border-border
                bg-muted overflow-hidden shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <WidgetFrame key={`fullscreen-${reloadKey}`} {...props} onRetry={reload} fillParent />
              <WidgetToolbar
                copied={copied}
                onCopy={handleCopy}
                onReload={reload}
                onFullscreen={closeFullscreen}
                isFullscreen={true}
              />
            </div>
          </div>,
          document.body,
        )}
    </div>
  )
}

interface WidgetFrameProps extends WidgetViewProps {
  onRetry: () => void
  fillParent?: boolean
}

function WidgetFrame({
  widgetCode,
  status,
  widgetId,
  title,
  width,
  height: initialHeight,
  onRetry,
  fillParent,
}: WidgetFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)
  const [height, setHeight] = useState(initialHeight ?? SKELETON_DEFAULT_HEIGHT)
  const seqRef = useRef(0)
  const latestRef = useRef('')
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Inject theme tokens + widget id into the shell. JSON.stringify supplies
  // quotes + standard escaping for the id; we further escape "<" to "\\u003c"
  // so a hypothetical "</script>" can't close the shell's script block. A
  // function replacement avoids String.replace's "$" special-handling. Each
  // placeholder appears exactly once. The initial theme is resolved from the
  // app's `.dark` class at mount; subsequent toggles are pushed via a `theme`
  // postMessage from the MutationObserver below (no remount needed).
  const srcDoc = useMemo(() => {
    const t = resolveThemeTokens(isAppDark())
    const idLiteral = JSON.stringify(widgetId).replace(/</g, '\\u003c')
    // Replace theme tokens FIRST (color strings never contain '%%'), then
    // widget_id LAST. This order makes the substitution unaffected by id
    // contents.
    return WIDGET_SHELL_HTML.replace('%%BG%%', () => t.bg)
      .replace('%%FG%%', () => t.fg)
      .replace('%%MUTED%%', () => t.muted)
      .replace('%%BORDER%%', () => t.border)
      .replace('%%ACCENT%%', () => t.accent)
      .replace('%%WIDGET_ID%%', () => idLiteral)
  }, [widgetId])

  const tooBig = new Blob([widgetCode]).size > MAX_CODE_BYTES
  // eslint-disable-next-line react-hooks/refs
  latestRef.current = widgetCode

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (e.source !== iframeRef.current?.contentWindow) return
      if (!e.data || typeof e.data !== 'object') return
      const d = e.data as { widgetId?: string; type?: string; height?: number; message?: string }
      if (d.widgetId !== widgetId) return
      if (d.type === 'ready') setReady(true)
      else if (d.type === 'error') setFailed(true)
      else if (d.type === 'resize' && typeof d.height === 'number') {
        setHeight(Math.min(Math.max(d.height, 40), MAX_HEIGHT_PX))
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [widgetId])

  useEffect(() => {
    if (ready || failed) return
    const t = setTimeout(() => setFailed(true), READY_TIMEOUT_MS)
    return () => clearTimeout(t)
  }, [ready, failed])

  // Push a `theme` message whenever the app toggles the .dark class on <html>.
  // Sandbox is opaque-origin, so the iframe can't observe the parent's class
  // change itself — we have to relay it. Stateless (no seq); the shell applies
  // it directly to :root CSS variables in place, so the rendered widget
  // recolors without remounting.
  //
  // We also re-push the CURRENT theme once `ready` flips true. The srcDoc is
  // built from a theme snapshot at render time; if the app toggled theme
  // between that snapshot and the iframe finishing morphdom load, the shell
  // would otherwise be stuck on the stale theme until the next toggle. Pushing
  // on ready closes that window.
  useEffect(() => {
    if (typeof document === 'undefined') return
    const html = document.documentElement
    const push = () => {
      const win = iframeRef.current?.contentWindow
      if (!win) return
      const t = resolveThemeTokens(html.classList.contains('dark'))
      win.postMessage({ widgetId, type: 'theme', ...t }, '*')
    }
    if (ready) push()
    const obs = new MutationObserver(push)
    obs.observe(html, { attributes: true, attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [widgetId, ready])

  useEffect(() => {
    if (!ready || failed || tooBig) return
    const send = (final: boolean) => {
      const win = iframeRef.current?.contentWindow
      if (!win) return
      seqRef.current += 1
      win.postMessage(
        { widgetId, seq: seqRef.current, type: 'morph', html: latestRef.current },
        '*',
      )
      if (final) {
        seqRef.current += 1
        win.postMessage({ widgetId, seq: seqRef.current, type: 'finalize' }, '*')
      }
    }
    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (status === 'complete') {
      send(true)
    } else {
      debounceRef.current = setTimeout(() => send(false), 120)
    }
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [widgetCode, status, ready, failed, tooBig, widgetId])

  if (failed || tooBig) {
    return (
      <div className="rounded-lg border border-border bg-muted p-4 text-sm space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div className="space-y-1">
            <div className="font-medium text-foreground">
              {tooBig ? 'Widget too large to render' : 'Widget failed to render'}
              {title ? ` — ${title}` : ''}
            </div>
            <div className="text-xs text-muted-foreground">
              {tooBig
                ? 'The generated code exceeds the size cap. Source is shown below.'
                : 'The widget runtime reported an error or never finished loading.'}
            </div>
          </div>
          {!tooBig && (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex items-center gap-1 rounded-md border border-border
                bg-background px-2 py-1 text-xs text-foreground hover:bg-accent
                hover:text-accent-foreground transition-colors"
            >
              <RotateCw className="size-3" /> Retry
            </button>
          )}
        </div>
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">Show source</summary>
          <pre className="mt-2 max-h-80 overflow-auto rounded bg-background p-2">
            <code>{widgetCode}</code>
          </pre>
        </details>
      </div>
    )
  }

  return (
    <iframe
      ref={iframeRef}
      title={title ?? 'widget'}
      sandbox="allow-scripts"
      srcDoc={srcDoc}
      style={
        fillParent
          ? { width: '100%', height: '100%', border: 'none' }
          : { width: width ? `${width}px` : '100%', height, border: 'none' }
      }
      className={cn('block bg-muted', !fillParent && 'rounded-lg')}
    />
  )
}

interface WidgetToolbarProps {
  copied: boolean
  onCopy: () => void
  onReload: () => void
  onFullscreen: () => void
  isFullscreen: boolean
}

function WidgetToolbar({
  copied,
  onCopy,
  onReload,
  onFullscreen,
  isFullscreen,
}: WidgetToolbarProps) {
  return (
    <div
      className={cn(
        'absolute right-2 top-2 z-10 flex items-center gap-1 rounded-md border border-border',
        'bg-background/70 px-1 py-0.5 backdrop-blur-sm transition-opacity',
        // Toolbar fades in on hover when inline, stays fully visible when fullscreen.
        isFullscreen ? 'opacity-100' : 'opacity-50 group-hover/widget:opacity-100',
      )}
    >
      <ToolbarButton
        label={copied ? 'Copied' : 'Copy source'}
        onClick={onCopy}
        icon={<Copy className="size-3.5" />}
      />
      <ToolbarButton label="Reload" onClick={onReload} icon={<RotateCw className="size-3.5" />} />
      <ToolbarButton
        label={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
        onClick={onFullscreen}
        icon={
          isFullscreen ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />
        }
      />
    </div>
  )
}

function ToolbarButton({
  label,
  onClick,
  icon,
}: {
  label: string
  onClick: () => void
  icon: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className="inline-flex items-center justify-center rounded p-1
        text-muted-foreground hover:text-foreground hover:bg-accent
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring transition-colors"
    >
      {icon}
    </button>
  )
}
