'use client'

import { memo } from 'react'
import {
  FileText,
  Globe,
  Code,
  Image,
  Database,
  File,
  Download,
  Package,
} from 'lucide-react'
import type { Artifact } from '@cubebox/core'

interface ArtifactCardProps {
  artifact: Artifact
  baseUrl?: string
}

const typeIcons: Record<string, typeof File> = {
  website: Globe,
  document: FileText,
  code: Code,
  image: Image,
  data: Database,
  file: File,
}

const typeLabels: Record<string, string> = {
  website: 'Website',
  document: 'Document',
  code: 'Code',
  image: 'Image',
  data: 'Data',
  file: 'File',
}

export const ArtifactCard = memo(function ArtifactCard({
  artifact,
  baseUrl = '',
}: ArtifactCardProps) {
  const Icon = typeIcons[artifact.artifact_type] ?? File
  const label = typeLabels[artifact.artifact_type] ?? 'File'

  const downloadUrl =
    `${baseUrl}/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/download`

  return (
    <div className="my-2 rounded-lg border border-border bg-card p-3">
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
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
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
        {downloadUrl && (
          <a
            href={downloadUrl}
            className="flex size-8 shrink-0 items-center justify-center rounded-md
              text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title="Download"
          >
            <Download className="size-4" />
          </a>
        )}
      </div>
    </div>
  )
})
