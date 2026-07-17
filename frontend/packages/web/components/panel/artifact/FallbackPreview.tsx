'use client'

import { File, Download } from 'lucide-react'
import type { Artifact } from '@cubeplex/core'
import { useTranslations } from 'next-intl'

import { buildDownloadUrl } from './previewUtils'

interface FallbackPreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

export function FallbackPreview({ artifact, version, workspaceId }: FallbackPreviewProps) {
  const t = useTranslations('panel.fallback')
  const tArtifact = useTranslations('panel.artifactPanel')
  const downloadUrl = buildDownloadUrl(artifact, workspaceId, version)

  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
      <div className="flex size-16 items-center justify-center rounded-xl bg-muted">
        <File className="size-8 text-muted-foreground" />
      </div>
      <div>
        <h3 className="text-sm font-medium text-foreground">{artifact.name}</h3>
        {artifact.description && (
          <p className="mt-1 text-xs text-muted-foreground">{artifact.description}</p>
        )}
        <p className="mt-1 text-xs text-muted-foreground/60">
          {artifact.mime_type || tArtifact('unknownType')} &middot; v{version ?? artifact.version}
        </p>
        <p className="mt-2 text-xs text-muted-foreground">{t('unsupported')}</p>
      </div>
      <a
        href={downloadUrl}
        className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2
          text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
      >
        <Download className="size-4" />
        {t('download')}
      </a>
    </div>
  )
}
