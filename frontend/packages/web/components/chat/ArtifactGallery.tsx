'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { useShallow } from 'zustand/react/shallow'
import { Package, ChevronDown, ChevronRight, Eye, Download, Loader2 } from 'lucide-react'
import { useArtifactStore, usePanelStore } from '@cubeplex/core'
import type { Artifact } from '@cubeplex/core'
import { getArtifactIcon } from '@/components/panel/artifact/artifactIcons'
import { buildDownloadUrl } from '@/components/panel/artifact/previewUtils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

interface ArtifactGalleryProps {
  conversationId: string
}

export function ArtifactGallery({ conversationId }: ArtifactGalleryProps) {
  const t = useTranslations('chat')
  const [isExpanded, setIsExpanded] = useState(false)
  const artifacts = useArtifactStore(useShallow((s) => s.getArtifacts(conversationId)))
  const isLoading = useArtifactStore((s) => s.isLoading(conversationId))
  const openPreview = usePanelStore((s) => s.openArtifact)

  if (artifacts.length === 0) return null

  return (
    <div className="border-b border-border bg-card/50">
      <button
        onClick={() => setIsExpanded((prev) => !prev)}
        className="w-full flex items-center gap-2 px-4 py-2 text-xs text-muted-foreground
          hover:text-foreground transition-colors"
      >
        {isExpanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        <Package className="size-3" />
        <span>{t('artifacts')}</span>
        {isLoading ? (
          <Loader2 className="size-3 animate-spin text-muted-foreground/70" />
        ) : (
          <span
            className="rounded-full bg-muted px-1.5 py-0.5 text-[10px]
            text-muted-foreground/70"
          >
            {artifacts.length}
          </span>
        )}
      </button>

      {isExpanded && (
        <div className="px-4 pb-3 grid gap-1.5">
          {isLoading && artifacts.length === 0
            ? Array.from({ length: 2 }).map((_, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md bg-background
                  border border-border/50"
                >
                  <div className="size-3.5 rounded bg-muted animate-pulse shrink-0" />
                  <div className="h-3 rounded bg-muted animate-pulse flex-1" />
                </div>
              ))
            : artifacts.map((artifact) => (
                <ArtifactGalleryItem
                  key={artifact.id}
                  artifact={artifact}
                  onPreview={() => openPreview(conversationId, artifact.id)}
                />
              ))}
        </div>
      )}
    </div>
  )
}

function ArtifactGalleryItem({
  artifact,
  onPreview,
}: {
  artifact: Artifact
  onPreview: () => void
}) {
  const tExtras = useTranslations('chatExtras')
  const Icon = getArtifactIcon(artifact)
  const { workspaceId } = useWorkspaceContext()
  const downloadUrl = workspaceId ? buildDownloadUrl(artifact, workspaceId) : '#'

  return (
    <div
      className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md bg-background
        border border-border/50 cursor-pointer hover:border-primary/30 transition-colors"
      onClick={onPreview}
    >
      {/* eslint-disable-next-line react-hooks/static-components */}
      <Icon className="size-3.5 text-primary/70 shrink-0" />
      <span className="text-xs font-medium text-foreground truncate flex-1">{artifact.name}</span>
      {artifact.version > 1 && (
        <span className="text-[10px] text-muted-foreground/60">v{artifact.version}</span>
      )}
      <div className="flex items-center gap-0.5 shrink-0">
        <button
          onClick={(e) => {
            e.stopPropagation()
            onPreview()
          }}
          className="p-1 rounded hover:bg-muted transition-colors"
          title={tExtras('preview')}
        >
          <Eye className="size-3 text-muted-foreground" />
        </button>
        <a
          href={downloadUrl}
          onClick={(e) => e.stopPropagation()}
          className="p-1 rounded hover:bg-muted transition-colors"
          title={tExtras('download')}
        >
          <Download className="size-3 text-muted-foreground" />
        </a>
      </div>
    </div>
  )
}
