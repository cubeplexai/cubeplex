'use client'

import { useState, useCallback } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { Artifact } from '@cubeplex/core'
import { buildPreviewUrl } from './previewUtils'
import { ImageViewer } from '@/components/shared/previews'

interface ImageCarouselProps {
  artifact: Artifact
  imageFiles: string[]
  version: number | null
  workspaceId: string
}

export function ImageCarousel({
  artifact,
  imageFiles,
  version,
  workspaceId,
}: ImageCarouselProps): React.ReactElement {
  const [index, setIndex] = useState(0)
  const count = imageFiles.length

  // Defensive clamp: the parent remounts this component on version changes,
  // so `index` stays in range in practice — but never read past the end.
  const safeIndex = Math.min(index, Math.max(0, count - 1))

  const go = useCallback(
    (delta: number) => {
      setIndex((i) => Math.min(count - 1, Math.max(0, i + delta)))
    },
    [count],
  )

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        e.preventDefault()
        go(-1)
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        go(1)
      }
    },
    [go],
  )

  const url = buildPreviewUrl(artifact, imageFiles[safeIndex], version, workspaceId)

  return (
    <div className="flex h-full flex-col" tabIndex={0} onKeyDown={onKeyDown}>
      <div className="relative flex-1 overflow-hidden">
        <ImageViewer url={url} alt={`${artifact.name} ${safeIndex + 1}/${count}`} />
        {count > 1 && (
          <>
            <button
              onClick={() => go(-1)}
              disabled={safeIndex === 0}
              aria-label="Previous image"
              className="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-background/70
                p-1.5 text-foreground backdrop-blur-sm transition-colors hover:bg-background
                disabled:opacity-30"
            >
              <ChevronLeft className="size-5" />
            </button>
            <button
              onClick={() => go(1)}
              disabled={safeIndex === count - 1}
              aria-label="Next image"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-background/70
                p-1.5 text-foreground backdrop-blur-sm transition-colors hover:bg-background
                disabled:opacity-30"
            >
              <ChevronRight className="size-5" />
            </button>
            <div
              className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full
              bg-background/70 px-2 py-0.5 text-xs tabular-nums text-muted-foreground
              backdrop-blur-sm"
            >
              {safeIndex + 1} / {count}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
