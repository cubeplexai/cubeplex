'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { WIDGET_SHELL_HTML } from './widgetShell'

const READY_TIMEOUT_MS = 5000
const MAX_HEIGHT_PX = 4000
const MAX_CODE_BYTES = 256 * 1024

interface WidgetViewProps {
  widgetCode: string
  status: 'streaming' | 'complete'
  widgetId: string
  title?: string
  width?: number
  height?: number
}

export function WidgetView({
  widgetCode,
  status,
  widgetId,
  title,
  width,
  height: initialHeight,
}: WidgetViewProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)
  const [height, setHeight] = useState(initialHeight ?? 120)
  const seqRef = useRef(0)
  const latestRef = useRef('')
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Inject the real id into the shell. JSON.stringify supplies quotes + standard
  // escaping; we further escape "<" to "\\u003c" so a hypothetical "</script>"
  // can't close the shell's script block. A function replacement avoids
  // String.replace's "$" special-handling. Each placeholder appears exactly
  // once in the shell. Theme tokens are resolved from the app's `.dark` class
  // (next-themes attribute="class") at mount; toggling theme after render
  // keeps the widget on the original theme until the conversation reloads.
  const srcDoc = useMemo(() => {
    const isDark =
      typeof document !== 'undefined' && document.documentElement.classList.contains('dark')
    const t = isDark
      ? { bg: '#0e1116', fg: '#e6edf3', muted: '#161b22', border: '#30363d', accent: '#58a6ff' }
      : { bg: '#ffffff', fg: '#0a0a0f', muted: '#f5f5f7', border: '#e5e7eb', accent: '#0061c2' }
    const idLiteral = JSON.stringify(widgetId).replace(/</g, '\\u003c')
    // Replace theme tokens FIRST (values are color strings like '#0e1116',
    // never contain '%%'), then widget_id LAST. Reversed order would let an
    // unlikely widgetId containing e.g. '%%BG%%' get clobbered by a later
    // theme replacement. This order makes the substitution unaffected by id
    // contents.
    return WIDGET_SHELL_HTML.replace('%%BG%%', () => t.bg)
      .replace('%%FG%%', () => t.fg)
      .replace('%%MUTED%%', () => t.muted)
      .replace('%%BORDER%%', () => t.border)
      .replace('%%ACCENT%%', () => t.accent)
      .replace('%%WIDGET_ID%%', () => idLiteral)
  }, [widgetId])

  const tooBig = new Blob([widgetCode]).size > MAX_CODE_BYTES
  latestRef.current = widgetCode

  // child -> parent listener (validate source + type)
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

  // readiness timeout -> fallback. If `ready` flips true, this effect re-runs,
  // early-returns, and the cleanup clears the pending timer.
  useEffect(() => {
    if (ready || failed) return
    const t = setTimeout(() => setFailed(true), READY_TIMEOUT_MS)
    return () => clearTimeout(t)
  }, [ready, failed])

  // push morph (debounced) once ready
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
      <details className="rounded-lg border border-border bg-muted p-2 text-sm">
        <summary className="cursor-pointer text-muted-foreground">
          {tooBig
            ? 'Widget too large — showing source'
            : 'Widget failed to render — showing source'}
          {title ? ` (${title})` : ''}
        </summary>
        <pre className="overflow-auto text-xs">
          <code>{widgetCode}</code>
        </pre>
      </details>
    )
  }

  return (
    <iframe
      ref={iframeRef}
      title={title ?? 'widget'}
      sandbox="allow-scripts"
      srcDoc={srcDoc}
      style={{ width: width ? `${width}px` : '100%', height, border: 'none' }}
      className="rounded-lg border border-border bg-muted"
    />
  )
}
