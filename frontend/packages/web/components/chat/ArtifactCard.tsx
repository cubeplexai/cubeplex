'use client'

import { memo, useCallback } from 'react'
import { Download, Package, Eye } from 'lucide-react'
import type { Artifact } from '@cubebox/core'
import { usePanelStore } from '@cubebox/core'
import { getArtifactIcon, getArtifactLabel } from '@/components/panel/artifact/artifactIcons'

interface ArtifactCardProps {
  artifact: Artifact
  baseUrl?: string
}

export const ArtifactCard = memo(function ArtifactCard({
  artifact,
  baseUrl = '',
}: ArtifactCardProps) {
  const Icon = getArtifactIcon(artifact)
  const label = getArtifactLabel(artifact)
  const openPreview = usePanelStore(s => s.openArtifact)

  const downloadUrl =
    `${baseUrl}/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/download`

  const handlePreview = useCallback(() => {
    openPreview(artifact.conversation_id, artifact.id)
  }, [openPreview, artifact.conversation_id, artifact.id])

  return (
    <div
      className="my-2 rounded-lg border border-border bg-card p-3 cursor-pointer
        transition-colors hover:border-primary/30 hover:bg-card/80"
      onClick={handlePreview}
    >
      <div className="flex items-center gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10">
          <Icon className="size-4 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground">
              {artifact.name}
            </span>
            {artifact.version > 1 && (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px]
                text-muted-foreground">
                v{artifact.version}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Package className="size-3" />
            <span>{label}</span>
            {artifact.description && (
              <>
                <span className="text-muted-foreground/40">|</span>
                <span className="truncate">{artifact.description}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={(e) => { e.stopPropagation(); handlePreview() }}
            className="flex size-8 items-center justify-center rounded-md
              text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title="Preview"
          >
            <Eye className="size-4" />
          </button>
          <a
            href={downloadUrl}
            onClick={(e) => e.stopPropagation()}
            className="flex size-8 items-center justify-center rounded-md
              text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title="Download"
          >
            <Download className="size-4" />
          </a>
        </div>
      </div>
    </div>
  )
})
