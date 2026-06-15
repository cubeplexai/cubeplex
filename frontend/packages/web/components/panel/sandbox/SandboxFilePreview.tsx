'use client'

import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { Download, FileText } from 'lucide-react'
import { csrfHeaders } from '@/lib/csrf'
import { useSandboxFileContent } from '@/hooks/useSandboxFileContent'
import type { SandboxFileEntry } from '@/hooks/useSandboxFiles'
import { PreviewLoading } from '@/components/panel/artifact/PreviewLoading'

const OFFICE_EXTENSIONS = new Set(['.docx', '.xlsx', '.pptx'])
const TEXT_EXTENSIONS = new Set([
  '.txt',
  '.md',
  '.py',
  '.js',
  '.ts',
  '.tsx',
  '.jsx',
  '.json',
  '.yaml',
  '.yml',
  '.toml',
  '.cfg',
  '.ini',
  '.sh',
  '.bash',
  '.zsh',
  '.css',
  '.scss',
  '.less',
  '.sql',
  '.rs',
  '.go',
  '.java',
  '.c',
  '.cpp',
  '.h',
  '.rb',
  '.php',
  '.swift',
  '.kt',
  '.r',
  '.lua',
  '.pl',
  '.ex',
  '.exs',
  '.vue',
  '.svelte',
  '.xml',
  '.csv',
  '.log',
  '.env',
  '.gitignore',
  '.dockerfile',
  '.makefile',
])

function getExtension(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

function isTextFile(name: string): boolean {
  const ext = getExtension(name)
  if (TEXT_EXTENSIONS.has(ext)) return true
  if (ext === '.html') return false // HTML gets iframe preview
  if (!ext) return true // extensionless files assumed text
  return false
}

interface SandboxFilePreviewProps {
  entry: SandboxFileEntry
  workspaceId: string
}

export function SandboxFilePreview({ entry, workspaceId }: SandboxFilePreviewProps) {
  const ext = getExtension(entry.name)

  if (OFFICE_EXTENSIONS.has(ext)) {
    return <OfficeFilePreview entry={entry} workspaceId={workspaceId} />
  }
  if (ext === '.html') {
    return <HtmlFilePreview entry={entry} workspaceId={workspaceId} />
  }
  if (isTextFile(entry.name)) {
    return <TextFilePreview entry={entry} workspaceId={workspaceId} />
  }
  return <FallbackPreview entry={entry} workspaceId={workspaceId} />
}

function TextFilePreview({ entry, workspaceId }: { entry: SandboxFileEntry; workspaceId: string }) {
  const { content, error, loading } = useSandboxFileContent(workspaceId, entry.path)

  if (loading) return <PreviewLoading />
  if (error?.message === 'FILE_TOO_LARGE') {
    return <FallbackPreview entry={entry} workspaceId={workspaceId} />
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

function HtmlFilePreview({ entry, workspaceId }: { entry: SandboxFileEntry; workspaceId: string }) {
  const { content, error, loading } = useSandboxFileContent(workspaceId, entry.path)
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

const OFFICE_LOAD_TIMEOUT_MS = 15_000

function OfficeFilePreview({
  entry,
  workspaceId,
}: {
  entry: SandboxFileEntry
  workspaceId: string
}) {
  const [viewerUrl, setViewerUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [timedOut, setTimedOut] = useState(false)
  const loadCountRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    let cancelled = false
    const fetchToken = async () => {
      try {
        const url =
          `/api/v1/ws/${workspaceId}` +
          `/sandbox/files/preview-token` +
          `?path=${encodeURIComponent(entry.path)}`
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
  }, [workspaceId, entry.path])

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
    if (loadCountRef.current > 1) {
      clearTimeout(timerRef.current)
    }
  }, [])

  const handleRetry = useCallback(() => {
    setTimedOut(false)
    setViewerUrl(null)
    setError(null)
  }, [])

  if (error) {
    return <FallbackPreview entry={entry} workspaceId={workspaceId} />
  }
  if (!viewerUrl) return <PreviewLoading />

  if (timedOut) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
        <p className="text-sm text-muted-foreground">Office preview timed out.</p>
        <button
          onClick={handleRetry}
          className={
            'rounded-md bg-primary px-4 py-2 text-sm' +
            ' font-medium text-primary-foreground' +
            ' hover:bg-primary/90'
          }
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <iframe
      src={viewerUrl}
      className="h-full w-full border-0"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      onLoad={handleIframeLoad}
    />
  )
}

function FallbackPreview({ entry, workspaceId }: { entry: SandboxFileEntry; workspaceId: string }) {
  const downloadUrl =
    `/api/v1/ws/${workspaceId}` +
    `/sandbox/files/download` +
    `?path=${encodeURIComponent(entry.path)}`
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
