'use client'

import { memo, useState, useCallback } from 'react'
import type { Artifact } from '@cubebox/core'
import { usePanelStore } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { buildPreviewUrl } from '@/components/panel/artifact/previewUtils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { cn } from '@/lib/utils'

interface ImageGenerationCardProps {
  prompt: string
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

function ImageGenerationCardImpl({ prompt, artifact }: ImageGenerationCardProps) {
  const t = useTranslations('chatExtras')
  const { workspaceId } = useWorkspaceContext()
  const openPreview = usePanelStore((s) => s.openArtifact)
  const [imgLoaded, setImgLoaded] = useState(false)

  const handleClick = useCallback(() => {
    if (!artifact) return
    openPreview(artifact.conversation_id, artifact.id)
  }, [openPreview, artifact])

  const filename = artifact?.path.split('/').pop() || 'image.png'
  const previewUrl =
    artifact && workspaceId
      ? buildPreviewUrl(artifact, filename, artifact.version, workspaceId)
      : null

  const showShimmer = !artifact || !previewUrl || !imgLoaded

  return (
    <div
      className={cn(
        'my-2 w-72 overflow-hidden rounded-xl border border-border bg-card',
        artifact && 'cursor-pointer transition-colors hover:border-primary/30',
      )}
      onClick={handleClick}
    >
      <div className="relative aspect-[4/3] bg-muted/30">
        {showShimmer && <Shimmer />}
        {previewUrl && (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={previewUrl}
            alt={prompt}
            className={cn(
              'size-full object-cover transition-opacity duration-300',
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
      </div>
      {prompt && (
        <div className="border-t border-border px-3 py-2">
          <p className="line-clamp-2 text-xs text-muted-foreground">{prompt}</p>
        </div>
      )}
    </div>
  )
}

export const ImageGenerationCard = memo(ImageGenerationCardImpl)
