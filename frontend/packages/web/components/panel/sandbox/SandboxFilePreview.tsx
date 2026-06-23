'use client'

import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import dynamic from 'next/dynamic'
import { Download, FileText } from 'lucide-react'
import { csrfHeaders } from '@/lib/csrf'
import { useSandboxFileContent } from '@/hooks/useSandboxFileContent'
import type { SandboxFileEntry } from '@/hooks/useSandboxFiles'
import { PreviewLoading } from '@/components/panel/artifact/PreviewLoading'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { CodeHighlight, ImageViewer, MediaPlayer, CsvTable } from '@/components/shared/previews'

const PdfPreview = dynamic(
  () => import('@/components/panel/artifact/PdfPreview').then((m) => m.PdfPreview),
  { ssr: false, loading: () => <PreviewLoading /> },
)

const OFFICE_EXTENSIONS = new Set(['.docx', '.xlsx', '.pptx'])

const IMAGE_EXTENSIONS = new Set([
  '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico', '.tiff', '.avif',
])

const VIDEO_EXTENSIONS = new Set(['.mp4', '.webm', '.mov', '.avi', '.mkv', '.ogv'])

const AUDIO_EXTENSIONS = new Set(['.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a', '.wma'])

const CODE_EXTENSIONS = new Set([
  '.py', '.js', '.ts', '.tsx', '.jsx', '.mjs', '.cjs',
  '.json', '.yaml', '.yml', '.toml', '.cfg', '.ini',
  '.sh', '.bash', '.zsh',
  '.css', '.scss', '.less', '.sql',
  '.rs', '.go', '.java', '.c', '.cpp', '.h', '.hpp',
  '.rb', '.php', '.swift', '.kt', '.cs',
  '.r', '.lua', '.pl', '.ex', '.exs',
  '.vue', '.svelte', '.xml',
  '.dockerfile', '.makefile',
])

const PLAIN_TEXT_EXTENSIONS = new Set(['.txt', '.log', '.env', '.gitignore'])

function getExtension(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

function buildDownloadUrl(
  workspaceId: string,
  path: string,
  conversationId?: string | null,
): string {
  const convQs = conversationId ? `&conversation_id=${encodeURIComponent(conversationId)}` : ''
  return (
    `/api/v1/ws/${workspaceId}/sandbox/files/download` +
    `?path=${encodeURIComponent(path)}${convQs}`
  )
}

interface SandboxFilePreviewProps {
  entry: SandboxFileEntry
  workspaceId: string
  conversationId?: string | null
  onNavigate?: (path: string) => void
}

export function SandboxFilePreview({
  entry,
  workspaceId,
  conversationId,
  onNavigate,
}: SandboxFilePreviewProps) {
  const ext = getExtension(entry.name)
  const downloadUrl = buildDownloadUrl(workspaceId, entry.path, conversationId)

  if (IMAGE_EXTENSIONS.has(ext)) {
    return <ImageViewer url={downloadUrl} alt={entry.name} />
  }

  if (ext === '.pdf') {
    return <PdfPreview fileUrl={downloadUrl} />
  }

  if (VIDEO_EXTENSIONS.has(ext)) {
    return <MediaPlayer url={downloadUrl} type="video" filename={entry.name} />
  }

  if (AUDIO_EXTENSIONS.has(ext)) {
    return <MediaPlayer url={downloadUrl} type="audio" filename={entry.name} />
  }

  if (OFFICE_EXTENSIONS.has(ext)) {
    return (
      <OfficeFilePreview
        entry={entry}
        workspaceId={workspaceId}
        conversationId={conversationId}
      />
    )
  }

  if (ext === '.html') {
    return (
      <HtmlFilePreview
        entry={entry}
        workspaceId={workspaceId}
        conversationId={conversationId}
      />
    )
  }

  if (ext === '.csv') {
    return (
      <CsvFilePreview
        entry={entry}
        workspaceId={workspaceId}
        conversationId={conversationId}
      />
    )
  }

  if (ext === '.md') {
    return (
      <MarkdownFilePreview
        entry={entry}
        workspaceId={workspaceId}
        conversationId={conversationId}
        onNavigate={onNavigate}
      />
    )
  }

  if (CODE_EXTENSIONS.has(ext)) {
    return (
      <CodeFilePreview
        entry={entry}
        workspaceId={workspaceId}
        conversationId={conversationId}
      />
    )
  }

  if (PLAIN_TEXT_EXTENSIONS.has(ext) || !ext) {
    return (
      <TextFilePreview
        entry={entry}
        workspaceId={workspaceId}
        conversationId={conversationId}
      />
    )
  }

  return (
    <FallbackPreview
      entry={entry}
      workspaceId={workspaceId}
      conversationId={conversationId}
    />
  )
}

// ── Text content previews (fetch via useSandboxFileContent) ───────

function CodeFilePreview({
  entry,
  workspaceId,
  conversationId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
  conversationId?: string | null
}) {
  const { content, error, loading } = useSandboxFileContent(
    workspaceId, entry.path, conversationId,
  )

  if (loading) return <PreviewLoading />
  if (error?.message === 'FILE_TOO_LARGE') {
    return (
      <FallbackPreview entry={entry} workspaceId={workspaceId} conversationId={conversationId} />
    )
  }
  if (error) {
    return <div className="p-4 text-sm text-destructive">Failed to load: {error.message}</div>
  }
  if (content == null) return null

  return <CodeHighlight code={content} filename={entry.name} />
}

function CsvFilePreview({
  entry,
  workspaceId,
  conversationId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
  conversationId?: string | null
}) {
  const { content, error, loading } = useSandboxFileContent(
    workspaceId, entry.path, conversationId,
  )

  if (loading) return <PreviewLoading />
  if (error?.message === 'FILE_TOO_LARGE') {
    return (
      <FallbackPreview entry={entry} workspaceId={workspaceId} conversationId={conversationId} />
    )
  }
  if (error) {
    return <div className="p-4 text-sm text-destructive">Failed to load: {error.message}</div>
  }
  if (content == null) return null

  return <CsvTable content={content} />
}

function MarkdownFilePreview({
  entry,
  workspaceId,
  conversationId,
  onNavigate,
}: {
  entry: SandboxFileEntry
  workspaceId: string
  conversationId?: string | null
  onNavigate?: (path: string) => void
}) {
  const { content, error, loading } = useSandboxFileContent(
    workspaceId, entry.path, conversationId,
  )

  const resolveAssetUrl = useCallback(
    (path: string) => buildDownloadUrl(workspaceId, path, conversationId),
    [workspaceId, conversationId],
  )

  if (loading) return <PreviewLoading />
  if (error?.message === 'FILE_TOO_LARGE') {
    return (
      <FallbackPreview entry={entry} workspaceId={workspaceId} conversationId={conversationId} />
    )
  }
  if (error) {
    return <div className="p-4 text-sm text-destructive">Failed to load: {error.message}</div>
  }
  if (content == null) return null

  return (
    <div className="h-full overflow-auto">
      <MarkdownWithCitations
        className="prose prose-sm dark:prose-invert max-w-none p-4"
        sandbox={{
          filePath: entry.path,
          onNavigate: onNavigate ?? (() => {}),
          resolveAssetUrl,
        }}
      >
        {content}
      </MarkdownWithCitations>
    </div>
  )
}

function TextFilePreview({
  entry,
  workspaceId,
  conversationId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
  conversationId?: string | null
}) {
  const { content, error, loading } = useSandboxFileContent(
    workspaceId, entry.path, conversationId,
  )

  if (loading) return <PreviewLoading />
  if (error?.message === 'FILE_TOO_LARGE') {
    return (
      <FallbackPreview entry={entry} workspaceId={workspaceId} conversationId={conversationId} />
    )
  }
  if (error) {
    return <div className="p-4 text-sm text-destructive">Failed to load: {error.message}</div>
  }

  return (
    <div className="h-full overflow-auto">
      <pre
        className={
          'p-4 text-xs leading-relaxed font-mono' +
          ' text-foreground whitespace-pre-wrap break-words'
        }
      >
        {content}
      </pre>
    </div>
  )
}

// ── HTML blob-URL iframe ──────────────────────────────────────────

function HtmlFilePreview({
  entry,
  workspaceId,
  conversationId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
  conversationId?: string | null
}) {
  const { content, error, loading } = useSandboxFileContent(
    workspaceId, entry.path, conversationId,
  )
  const blobUrl = useMemo(() => {
    if (!content) return null
    const blob = new Blob([content], { type: 'text/html' })
    return URL.createObjectURL(blob)
  }, [content])

  useEffect(() => {
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [blobUrl])

  if (loading) return <PreviewLoading />
  if (error) {
    return <div className="p-4 text-sm text-destructive">Failed to load: {error.message}</div>
  }
  if (!blobUrl) return null

  return (
    <iframe
      title={`Preview: ${entry.name}`}
      src={blobUrl}
      className="h-full w-full border-0"
      sandbox="allow-scripts"
    />
  )
}

// ── Office Online Viewer ──────────────────────────────────────────

const OFFICE_LOAD_TIMEOUT_MS = 15_000

function OfficeFilePreview({
  entry,
  workspaceId,
  conversationId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
  conversationId?: string | null
}) {
  const [viewerUrl, setViewerUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [timedOut, setTimedOut] = useState(false)
  const [retrySeq, setRetrySeq] = useState(0)
  const loadCountRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    let cancelled = false
    const fetchToken = async () => {
      try {
        const convQs = conversationId
          ? `&conversation_id=${encodeURIComponent(conversationId)}`
          : ''
        const url =
          `/api/v1/ws/${workspaceId}` +
          `/sandbox/files/preview-token` +
          `?path=${encodeURIComponent(entry.path)}${convQs}`
        const res = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          headers: csrfHeaders(),
        })
        if (!res.ok) throw new Error(`${res.status}`)
        const data = (await res.json()) as { viewer_url: string }
        if (!cancelled) setViewerUrl(data.viewer_url)
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    }
    void fetchToken()
    return () => {
      cancelled = true
    }
  }, [workspaceId, conversationId, entry.path, retrySeq])

  useEffect(() => {
    if (!viewerUrl) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTimedOut(false)
    loadCountRef.current = 0
    timerRef.current = setTimeout(() => setTimedOut(true), OFFICE_LOAD_TIMEOUT_MS)
    return () => clearTimeout(timerRef.current)
  }, [viewerUrl])

  const handleIframeLoad = useCallback(() => {
    loadCountRef.current += 1
    clearTimeout(timerRef.current)
  }, [])

  const handleRetry = useCallback(() => {
    setTimedOut(false)
    setViewerUrl(null)
    setError(null)
    setRetrySeq((n) => n + 1)
  }, [])

  if (error || timedOut) {
    const downloadUrl = buildDownloadUrl(workspaceId, entry.path, conversationId)
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
        <div className="flex size-16 items-center justify-center rounded-xl bg-muted">
          <FileText className="size-8 text-muted-foreground" />
        </div>
        <div>
          <h3 className="text-sm font-medium text-foreground">{entry.name}</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {timedOut ? 'Office preview timed out.' : 'Failed to load preview.'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRetry}
            className={
              'rounded-md border border-border px-4 py-2 text-sm' +
              ' font-medium text-foreground hover:bg-muted transition-colors'
            }
          >
            Retry
          </button>
          <a
            href={downloadUrl}
            className={
              'inline-flex items-center gap-2 rounded-md' +
              ' bg-primary px-4 py-2 text-sm font-medium' +
              ' text-primary-foreground hover:bg-primary/90 transition-colors'
            }
          >
            <Download className="size-4" />
            Download
          </a>
        </div>
      </div>
    )
  }
  if (!viewerUrl) return <PreviewLoading />

  return (
    <iframe
      src={viewerUrl}
      className="h-full w-full border-0"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      onLoad={handleIframeLoad}
    />
  )
}

// ── Fallback download ─────────────────────────────────────────────

function FallbackPreview({
  entry,
  workspaceId,
  conversationId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
  conversationId?: string | null
}) {
  const downloadUrl = buildDownloadUrl(workspaceId, entry.path, conversationId)
  const sizeLabel =
    entry.size < 1024
      ? `${entry.size} B`
      : entry.size < 1_048_576
        ? `${(entry.size / 1024).toFixed(1)} KB`
        : `${(entry.size / 1_048_576).toFixed(1)} MB`

  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="flex size-16 items-center justify-center rounded-xl bg-muted">
        <FileText className="size-8 text-muted-foreground" />
      </div>
      <div>
        <h3 className="text-sm font-medium text-foreground">{entry.name}</h3>
        <p className="mt-1 text-xs text-muted-foreground">{sizeLabel}</p>
      </div>
      <a
        href={downloadUrl}
        className={
          'inline-flex items-center gap-2 rounded-md' +
          ' bg-primary px-4 py-2 text-sm font-medium' +
          ' text-primary-foreground hover:bg-primary/90' +
          ' transition-colors'
        }
      >
        <Download className="size-4" />
        Download
      </a>
    </div>
  )
}
