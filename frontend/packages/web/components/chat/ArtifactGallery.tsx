'use client'

import { useState } from 'react'
import {
  Package, ChevronDown, ChevronRight, Globe, FileText, Code, Image, Database, File, Eye,
  Download,
} from 'lucide-react'
import { useArtifactStore } from '@cubebox/core'
import type { Artifact } from '@cubebox/core'

const typeIcons: Record<string, typeof File> = {
  website: Globe,
  document: FileText,
  code: Code,
  image: Image,
  data: Database,
  file: File,
}

interface ArtifactGalleryProps {
  conversationId: string
}

export function ArtifactGallery({ conversationId }: ArtifactGalleryProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const artifacts = useArtifactStore(s => s.getArtifacts(conversationId))
  const openPreview = useArtifactStore(s => s.openPreview)

  if (artifacts.length === 0) return null

  return (
    <div className="border-b border-border bg-card/50">
      <button
        onClick={() => setIsExpanded(prev => !prev)}
        className="w-full flex items-center gap-2 px-4 py-2 text-xs text-muted-foreground
          hover:text-foreground transition-colors"
      >
        {isExpanded ? (
          <ChevronDown className="size-3" />
        ) : (
          <ChevronRight className="size-3" />
        )}
        <Package className="size-3" />
        <span>Artifacts</span>
        <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px]
          text-muted-foreground/70">
          {artifacts.length}
        </span>
      </button>

      {isExpanded && (
        <div className="px-4 pb-3 grid gap-1.5">
          {artifacts.map(artifact => (
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

function ArtifactGalleryItem(
  { artifact, onPreview }: { artifact: Artifact; onPreview: () => void },
) {
  const Icon = typeIcons[artifact.artifact_type] ?? File
  const downloadUrl =
    `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/download`

  return (
    <div
      className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md bg-background
        border border-border/50 cursor-pointer hover:border-primary/30 transition-colors"
      onClick={onPreview}
    >
      <Icon className="size-3.5 text-primary/70 shrink-0" />
      <span className="text-xs font-medium text-foreground truncate flex-1">
        {artifact.name}
      </span>
      {artifact.version > 1 && (
        <span className="text-[10px] text-muted-foreground/60">v{artifact.version}</span>
      )}
      <div className="flex items-center gap-0.5 shrink-0">
        <button
          onClick={(e) => { e.stopPropagation(); onPreview() }}
          className="p-1 rounded hover:bg-muted transition-colors"
          title="Preview"
        >
          <Eye className="size-3 text-muted-foreground" />
        </button>
        <a
          href={downloadUrl}
          onClick={(e) => e.stopPropagation()}
          className="p-1 rounded hover:bg-muted transition-colors"
          title="Download"
        >
          <Download className="size-3 text-muted-foreground" />
        </a>
      </div>
    </div>
  )
}
