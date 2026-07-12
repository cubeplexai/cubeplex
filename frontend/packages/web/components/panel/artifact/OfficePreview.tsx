'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { Download, RefreshCw } from 'lucide-react'
import type { Artifact } from '@cubeplex/core'
import { createApiClient, requestPreviewToken } from '@cubeplex/core'
import { useTranslations } from 'next-intl'
import { getArtifactIcon } from './artifactIcons'
import { buildDownloadUrl } from './previewUtils'
import { PreviewLoading } from './PreviewLoading'

interface OfficePreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

type ViewerState = 'loading' | 'ready' | 'error'

const LOAD_TIMEOUT_MS = 15_000
const REDIRECT_DETECT_MS = 1_500

export function OfficePreview({ artifact, version, workspaceId }: OfficePreviewProps) {
  const t = useTranslations('panel.office')
  const [viewerUrl, setViewerUrl] = useState<string | null>(null)
  const [state, setState] = useState<ViewerState>('loading')
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const loadCountRef = useRef(0)

  const fetchToken = useCallback(async () => {
    setState('loading')
    setViewerUrl(null)
    loadCountRef.current = 0
    try {
      const client = createApiClient('')
      if (workspaceId) client.setWorkspaceId(workspaceId)
      const res = await requestPreviewToken(
        client,
        artifact.conversation_id,
        artifact.id,
        version ?? artifact.version,
      )
      setViewerUrl(res.viewer_url)
    } catch {
      setState('error')
    }
  }, [artifact.conversation_id, artifact.id, artifact.version, version, workspaceId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchToken()
  }, [fetchToken])

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
    const Icon = getArtifactIcon(artifact)
    const downloadUrl = buildDownloadUrl(artifact, workspaceId, version)
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
        <div className="flex size-16 items-center justify-center rounded-xl bg-muted">
          {/* eslint-disable-next-line react-hooks/static-components */}
          <Icon className="size-8 text-muted-foreground" />
        </div>
        <div>
          <h3 className="text-sm font-medium text-foreground">{artifact.name}</h3>
          <p className="mt-2 text-sm text-muted-foreground">{t('error')}</p>
          <p className="mt-1 text-xs text-muted-foreground/60">{t('errorHint')}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void fetchToken()}
            className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2
              text-sm font-medium text-foreground hover:bg-muted transition-colors"
          >
            <RefreshCw className="size-4" />
            {t('retry')}
          </button>
          <a
            href={downloadUrl}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2
              text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <Download className="size-4" />
            {t('download')}
          </a>
        </div>
      </div>
    )
  }

  return (
    <div className="relative w-full h-full">
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
