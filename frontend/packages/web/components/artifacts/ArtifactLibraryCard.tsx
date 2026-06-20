'use client'

import { useCallback } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { MoreVertical, Download, ExternalLink, Trash2 } from 'lucide-react'
import { usePanelStore } from '@cubebox/core'
import type { Artifact } from '@cubebox/core'
import { getArtifactIcon } from '@/components/panel/artifact/artifactIcons'
import { buildDownloadUrl } from '@/components/panel/artifact/previewUtils'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

interface ArtifactLibraryCardProps {
  artifact: Artifact
  workspaceId: string
  onDelete: (artifact: Artifact) => void
}

export function ArtifactLibraryCard({
  artifact,
  workspaceId,
  onDelete,
}: ArtifactLibraryCardProps): React.ReactElement {
  const t = useTranslations('artifactsPage')
  const openArtifact = usePanelStore((s) => s.openArtifact)
  const Icon = getArtifactIcon(artifact)
  const conversationHref = `/w/${workspaceId}/conversations/${artifact.conversation_id}`

  const handlePreview = useCallback(() => {
    openArtifact(artifact.conversation_id, artifact.id)
  }, [openArtifact, artifact.conversation_id, artifact.id])

  return (
    <div
      onClick={handlePreview}
      className={cn(
        'group relative flex cursor-pointer flex-col gap-3 rounded-xl border border-border',
        'bg-card p-4 transition-all hover:border-primary/30 hover:shadow-sm',
      )}
      data-testid="artifact-card"
    >
      <div className="flex items-start justify-between">
        <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10">
          {/* eslint-disable-next-line react-hooks/static-components -- Icon is a component reference from getArtifactIcon */}
          <Icon className="size-5 text-primary" />
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger
            onClick={(e) => e.stopPropagation()}
            className="rounded-md p-1 text-muted-foreground opacity-0 transition-opacity
              hover:bg-muted hover:text-foreground group-hover:opacity-100
              data-[popup-open]:opacity-100"
            aria-label={t('preview')}
            data-testid="artifact-card-menu"
          >
            <MoreVertical className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
            <DropdownMenuItem
              render={<a href={buildDownloadUrl(artifact, workspaceId)} download />}
            >
              <Download className="mr-2 size-4" />
              {t('download')}
            </DropdownMenuItem>
            <DropdownMenuItem render={<Link href={conversationHref} />}>
              <ExternalLink className="mr-2 size-4" />
              {t('openSource')}
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              onClick={() => onDelete(artifact)}
              data-testid="artifact-card-delete"
            >
              <Trash2 className="mr-2 size-4" />
              {t('delete')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-foreground">{artifact.name}</span>
          {artifact.version > 1 && (
            <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              v{artifact.version}
            </span>
          )}
        </div>
        <div className="mt-0.5 flex items-center gap-1.5 text-xs capitalize text-muted-foreground">
          <span>{artifact.artifact_type}</span>
        </div>
        {artifact.description && (
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground/80">
            {artifact.description}
          </p>
        )}
      </div>
    </div>
  )
}
