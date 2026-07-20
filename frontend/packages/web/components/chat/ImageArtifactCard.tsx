'use client'

import { memo, useState, useCallback } from 'react'
import type { Artifact } from '@cubeplex/core'
import { usePanelStore } from '@cubeplex/core'
import { useTranslations } from 'next-intl'

import { useArtifactCover } from '@/components/panel/artifact/useArtifactCover'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { cn } from '@/lib/utils'

interface ImageArtifactCardProps {
  /** Displayed below the image as a caption. */
  caption: string
  /** Null while the artifact is still being generated/saved. */
  artifact: Artifact | null
}

function Shimmer() {
  return (
    <div className="absolute inset-0 overflow-hidden">
      <div
        className="absolute inset-0 animate-[shimmer_1.5s_linear_infinite]
          bg-gradient-to-r from-transparent via-foreground/[0.04] to-transparent"
      />
    </div>
  )
}

function ImageArtifactCardImpl({ caption, artifact }: ImageArtifactCardProps) {
  const t = useTranslations('chatExtras')
  const { workspaceId } = useWorkspaceContext()
  const openPreview = usePanelStore((s) => s.openArtifact)
  const [imgLoaded, setImgLoaded] = useState(false)

  const handleClick = useCallback(() => {
    if (!artifact) return
    openPreview(artifact.conversation_id, artifact.id)
  }, [openPreview, artifact])

  const cover = useArtifactCover(artifact, workspaceId)
  const previewUrl = cover.coverUrl

  const showShimmer = !artifact || !previewUrl || !imgLoaded

  return (
    <div
      className={cn(
        'my-2 w-full overflow-hidden rounded border border-border bg-card',
        artifact && 'cursor-pointer transition-colors hover:border-primary/30',
      )}
      onClick={handleClick}
    >
      <div className="relative bg-muted/30">
        {showShimmer && !previewUrl && (
          <div className="aspect-[4/3]">
            <Shimmer />
          </div>
        )}
        {previewUrl && (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={previewUrl}
            alt={caption}
            className={cn(
              'w-full h-auto transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onLoad={() => setImgLoaded(true)}
          />
        )}
        {!artifact && (
          <div className="absolute inset-x-0 bottom-3 flex justify-center">
            <span className="rounded-full bg-background/70 px-3 py-1 text-xs text-muted-foreground backdrop-blur-sm">
              {t('imageGenerating')}
            </span>
          </div>
        )}
        {cover.count > 1 && previewUrl && (
          <span
            className="absolute bottom-2 right-2 rounded-full bg-background/80 px-1.5
              py-0.5 text-[10px] font-medium text-muted-foreground backdrop-blur-sm"
          >
            ×{cover.count}
          </span>
        )}
      </div>
      {caption && (
        <div className="border-t border-border px-3 py-2">
          <p className="line-clamp-2 text-xs text-muted-foreground">{caption}</p>
        </div>
      )}
    </div>
  )
}

export const ImageArtifactCard = memo(ImageArtifactCardImpl)
