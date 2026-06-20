'use client'

import { useCallback, useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { MoreVertical, Download, ExternalLink, Trash2 } from 'lucide-react'
import { usePanelStore } from '@cubebox/core'
import type { Artifact } from '@cubebox/core'
import { getArtifactIcon } from '@/components/panel/artifact/artifactIcons'
import { buildDownloadUrl, buildPreviewUrl } from '@/components/panel/artifact/previewUtils'
import { ArtifactHtmlThumb } from './ArtifactHtmlThumb'
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

function isImageArtifact(artifact: Artifact): boolean {
  return artifact.artifact_type === 'image' || (artifact.mime_type?.startsWith('image/') ?? false)
}

function isHtmlArtifact(artifact: Artifact): boolean {
  return artifact.artifact_type === 'website' || artifact.mime_type === 'text/html'
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

  const [thumbFailed, setThumbFailed] = useState(false)
  const showImage = isImageArtifact(artifact) && !thumbFailed
  const showHtml = !showImage && isHtmlArtifact(artifact)
  // Website artifacts default to index.html (matching HtmlPreview); others use
  // the explicit entry file or the path basename.
  const fallbackFile = isHtmlArtifact(artifact) ? 'index.html' : ''
  const thumbFile = artifact.entry_file || artifact.path.split('/').pop() || fallbackFile
  const thumbUrl = buildPreviewUrl(artifact, thumbFile, null, workspaceId)

  const handlePreview = useCallback(() => {
    openArtifact(artifact.conversation_id, artifact.id)
  }, [openArtifact, artifact.conversation_id, artifact.id])

  return (
    <div
      onClick={handlePreview}
      className={cn(
        'group relative flex cursor-pointer flex-col overflow-hidden rounded-xl border border-border',
        'bg-card transition-all hover:border-primary/30 hover:shadow-sm',
      )}
      data-testid="artifact-card"
    >
      <div className="relative aspect-video w-full overflow-hidden bg-muted/40">
        {showImage ? (
          // eslint-disable-next-line @next/next/no-img-element -- artifact preview is an authed same-origin URL, not a static asset
          <img
            src={thumbUrl}
            alt={artifact.name}
            loading="lazy"
            onError={() => setThumbFailed(true)}
            className="size-full object-cover"
            data-testid="artifact-card-thumb"
          />
        ) : showHtml ? (
          <ArtifactHtmlThumb src={thumbUrl} title={artifact.name} />
        ) : (
          <div className="flex size-full items-center justify-center">
            {/* eslint-disable-next-line react-hooks/static-components -- Icon is a component reference from getArtifactIcon */}
            <Icon className="size-8 text-muted-foreground/60" />
          </div>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger
            onClick={(e) => e.stopPropagation()}
            className="absolute right-2 top-2 rounded-md bg-background/70 p-1 text-muted-foreground
              opacity-0 backdrop-blur-sm transition-opacity hover:bg-background hover:text-foreground
              group-hover:opacity-100 data-[popup-open]:opacity-100"
            aria-label={t('moreActions')}
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
      <div className="min-w-0 p-3">
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
