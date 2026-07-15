'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import { Download, RefreshCw } from 'lucide-react'
import type { AttachmentPanelInfo } from '@cubeplex/core'
import { createApiClient, requestAttachmentPreviewToken, usePanelStore } from '@cubeplex/core'
import { useLocale, useTranslations } from 'next-intl'

import { ScrollArea } from '@/components/ui/scroll-area'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { PreviewLoading } from '@/components/panel/artifact/PreviewLoading'
import { PanelHeader } from '@/components/panel/PanelHeader'
import { getFileVisual } from '@/lib/fileIcons'
import { cn } from '@/lib/utils'

const PdfPreview = dynamic(
  () => import('@/components/panel/artifact/PdfPreview').then((m) => m.PdfPreview),
  {
    ssr: false,
    loading: () => <PreviewLoading />,
  },
)

const TEXT_MAX_BYTES = 5 * 1024 * 1024
const TEXT_FAMILIES = new Set(['markdown', 'text', 'code', 'json', 'csv'])
const OFFICE_FAMILIES = new Set(['word', 'excel', 'ppt'])
const LOAD_TIMEOUT_MS = 15_000
const REDIRECT_DETECT_MS = 1_500

interface Props {
  info: AttachmentPanelInfo
}

function humanSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function AttachmentPreviewView({ info }: Props): React.ReactElement {
  const t = useTranslations('panel.attachment')
  const close = usePanelStore((s) => s.close)
  const visual = getFileVisual({ filename: info.filename, mime_type: info.mimeType })

  return (
    <div className="flex h-full flex-col bg-background">
      <PanelHeader
        source={{
          kind: 'plain',
          icon: (
            <div className={cn('size-5 grid place-items-center rounded-xs shrink-0', visual.bg)}>
              <visual.Icon className={cn('size-3', visual.fg)} />
            </div>
          ),
          title: info.filename,
          subtitle: `${visual.label} · ${humanSize(info.sizeBytes)}`,
        }}
        actions={
          <a
            href={info.downloadUrl}
            download
            className="p-1 rounded-xs hover:bg-accent transition-colors duration-fast"
            aria-label={t('download')}
          >
            <Download className="size-3.5 text-muted-foreground" />
          </a>
        }
        onClose={close}
      />
      <Body info={info} family={visual.family} />
    </div>
  )
}

function Body({ info, family }: { info: AttachmentPanelInfo; family: string }): React.ReactElement {
  const t = useTranslations('panel.attachment')
  const visual = getFileVisual({ filename: info.filename, mime_type: info.mimeType })
  if (family === 'pdf') {
    return (
      <div className="flex-1 min-h-0">
        <PdfPreview fileUrl={info.downloadUrl} />
      </div>
    )
  }
  if (family === 'video') {
    return (
      <div className="flex-1 grid place-items-center bg-black">
        <video src={info.downloadUrl} controls className="max-h-full max-w-full" />
      </div>
    )
  }
  if (family === 'audio') {
    return (
      <div className="flex-1 grid place-items-center p-8">
        <audio src={info.downloadUrl} controls />
      </div>
    )
  }
  if (OFFICE_FAMILIES.has(family) && info.conversationId && info.attachmentId) {
    return (
      <OfficeAttachmentPreview info={info} visual={visual} conversationId={info.conversationId} />
    )
  }
  if (!TEXT_FAMILIES.has(family)) {
    return (
      <div className="flex flex-col items-center justify-center flex-1 gap-4 p-8 text-center">
        <div className={cn('size-16 grid place-items-center rounded-xl', visual.bg)}>
          <visual.Icon className={cn('size-8', visual.fg)} />
        </div>
        <div>
          <p className="text-sm font-medium text-foreground">{info.filename}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {visual.label} · {humanSize(info.sizeBytes)}
          </p>
        </div>
        <a
          href={info.downloadUrl}
          download
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          <Download className="size-4" />
          {t('download')}
        </a>
      </div>
    )
  }
  if (info.sizeBytes > TEXT_MAX_BYTES) {
    return (
      <div className="flex-1 grid place-items-center p-8 text-center text-sm text-muted-foreground">
        {t('tooLarge', { size: (info.sizeBytes / 1024 / 1024).toFixed(1) })}
      </div>
    )
  }
  return <TextBody info={info} family={family} />
}

function TextBody({
  info,
  family,
}: {
  info: AttachmentPanelInfo
  family: string
}): React.ReactElement {
  const t = useTranslations('panel.attachment')
  const [state, setState] = useState<
    { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; text: string }
  >({ kind: 'loading' })

  useEffect(() => {
    const ac = new AbortController()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState({ kind: 'loading' })
    void (async () => {
      try {
        const res = await fetch(info.downloadUrl, {
          credentials: 'include',
          signal: ac.signal,
        })
        if (!res.ok) {
          setState({ kind: 'error', message: `HTTP ${res.status}` })
          return
        }
        const text = await res.text()
        if (!ac.signal.aborted) setState({ kind: 'ready', text })
      } catch (err) {
        if (!ac.signal.aborted) {
          setState({ kind: 'error', message: (err as Error).message ?? 'Failed to load' })
        }
      }
    })()
    return () => ac.abort()
  }, [info.downloadUrl])

  if (state.kind === 'loading') {
    return (
      <div className="flex-1 grid place-items-center p-8 text-sm text-muted-foreground">
        {t('loading')}
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <div className="flex-1 grid place-items-center p-8 text-sm text-destructive">
        {t('loadFailed', { message: state.message })}
      </div>
    )
  }
  return (
    <ScrollArea className="flex-1 p-4">
      {family === 'markdown' ? (
        <MarkdownWithCitations className="prose prose-sm dark:prose-invert max-w-none">
          {state.text}
        </MarkdownWithCitations>
      ) : family === 'csv' ? (
        <CsvTable text={state.text} />
      ) : family === 'json' ? (
        <pre className="font-mono text-sm whitespace-pre-wrap break-all">
          {prettyJson(state.text)}
        </pre>
      ) : (
        <pre className="font-mono text-sm whitespace-pre-wrap break-all">{state.text}</pre>
      )}
    </ScrollArea>
  )
}

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function CsvTable({ text }: { text: string }): React.ReactElement {
  const t = useTranslations('panel.attachment')
  const lines = text.split(/\r?\n/).filter(Boolean).slice(0, 1000)
  if (lines.length === 0)
    return <div className="p-4 text-sm text-muted-foreground">{t('empty')}</div>
  const rows = lines.map((line) => line.split(','))
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={i === 0 ? 'bg-muted font-medium' : ''}>
              {row.map((cell, j) => (
                <td key={j} className="border border-border px-2 py-1 align-top">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

type ViewerState = 'loading' | 'ready' | 'error'

function OfficeAttachmentPreview({
  info,
  visual,
  conversationId,
}: {
  info: AttachmentPanelInfo
  visual: ReturnType<typeof getFileVisual>
  conversationId: string
}): React.ReactElement {
  const t = useTranslations('panel.attachment')
  const locale = useLocale()
  const msLocale = locale === 'zh' ? 'zh-CN' : 'en-US'
  const [viewerUrl, setViewerUrl] = useState<string | null>(null)
  const [state, setState] = useState<ViewerState>('loading')
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const loadCountRef = useRef(0)
  const fetchedKeyRef = useRef<string | null>(null)

  const fetchToken = useCallback(async () => {
    setState('loading')
    setViewerUrl(null)
    loadCountRef.current = 0
    try {
      const client = createApiClient('')
      if (info.workspaceId) client.setWorkspaceId(info.workspaceId)
      const res = await requestAttachmentPreviewToken(client, conversationId, info.attachmentId)
      setViewerUrl(`${res.viewer_url}&ui=${msLocale}`)
    } catch {
      setState('error')
    }
  }, [conversationId, info.attachmentId, info.workspaceId, msLocale])

  useEffect(() => {
    // StrictMode double-invokes effects; key-guard so one open mints one
    // token (each mint triggers a Microsoft fetch of the document).
    const key = `${conversationId}:${info.attachmentId}`
    if (fetchedKeyRef.current === key) return
    fetchedKeyRef.current = key
    void fetchToken()
  }, [conversationId, info.attachmentId, fetchToken])

  useEffect(() => {
    if (!viewerUrl) return
    timerRef.current = setTimeout(() => {
      setState((prev) => (prev === 'loading' ? 'error' : prev))
    }, LOAD_TIMEOUT_MS)
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [viewerUrl])

  const handleLoad = () => {
    loadCountRef.current += 1
    if (timerRef.current) clearTimeout(timerRef.current)
    if (loadCountRef.current > 1) {
      setState('error')
      return
    }
    timerRef.current = setTimeout(() => {
      setState('ready')
    }, REDIRECT_DETECT_MS)
  }

  const handleError = () => {
    if (timerRef.current) clearTimeout(timerRef.current)
    setState('error')
  }

  if (state === 'error') {
    return (
      <div className="flex flex-col items-center justify-center flex-1 gap-4 p-8 text-center">
        <div className={cn('size-16 grid place-items-center rounded-xl', visual.bg)}>
          <visual.Icon className={cn('size-8', visual.fg)} />
        </div>
        <div>
          <p className="text-sm font-medium text-foreground">{info.filename}</p>
          <p className="mt-2 text-sm text-muted-foreground">{t('previewFailed')}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void fetchToken()}
            className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-muted transition-colors"
          >
            <RefreshCw className="size-4" />
            {t('retry')}
          </button>
          <a
            href={info.downloadUrl}
            download
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <Download className="size-4" />
            {t('download')}
          </a>
        </div>
      </div>
    )
  }

  return (
    <div className="relative w-full flex-1">
      {state === 'loading' && !viewerUrl && (
        <div className="absolute inset-0 z-10">
          <PreviewLoading />
        </div>
      )}
      {viewerUrl && (
        <iframe
          ref={iframeRef}
          src={viewerUrl}
          className="w-full h-full border-0"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          onLoad={handleLoad}
          onError={handleError}
        />
      )}
    </div>
  )
}
