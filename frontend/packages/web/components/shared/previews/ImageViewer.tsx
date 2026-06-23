'use client'

import { useState, useCallback } from 'react'
import { ZoomIn, ZoomOut, RotateCcw } from 'lucide-react'
import { PreviewLoading } from '@/components/panel/artifact/PreviewLoading'

interface ImageViewerProps {
  url: string
  alt?: string
}

export function ImageViewer({ url, alt = 'preview' }: ImageViewerProps) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [scale, setScale] = useState(1)
  const [naturalSize, setNaturalSize] = useState<{ w: number; h: number } | null>(null)

  const handleLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget
    setNaturalSize({ w: img.naturalWidth, h: img.naturalHeight })
    setLoading(false)
  }, [])

  const handleWheel = useCallback((e: React.WheelEvent) => {
    if (!e.ctrlKey && !e.metaKey) return
    e.preventDefault()
    setScale((s) => {
      const delta = e.deltaY > 0 ? -0.1 : 0.1
      return Math.max(0.25, Math.min(4, +(s + delta).toFixed(2)))
    })
  }, [])

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-sm text-destructive">
        Failed to load image
      </div>
    )
  }

  const zoomed = scale !== 1

  return (
    <div className="flex h-full flex-col">
      {zoomed && (
        <div
          className="flex items-center justify-end gap-1 px-3 py-1.5 border-b
            border-border bg-muted/30 shrink-0"
        >
          <button
            onClick={() => setScale((s) => Math.max(0.25, +(s - 0.25).toFixed(2)))}
            disabled={scale <= 0.25}
            className="p-1 rounded hover:bg-muted disabled:opacity-30 transition-colors"
          >
            <ZoomOut className="size-4 text-foreground" />
          </button>
          <span className="text-xs text-muted-foreground tabular-nums min-w-[3rem] text-center">
            {Math.round(scale * 100)}%
          </span>
          <button
            onClick={() => setScale((s) => Math.min(4, +(s + 0.25).toFixed(2)))}
            disabled={scale >= 4}
            className="p-1 rounded hover:bg-muted disabled:opacity-30 transition-colors"
          >
            <ZoomIn className="size-4 text-foreground" />
          </button>
          <button
            onClick={() => setScale(1)}
            className="p-1 rounded hover:bg-muted transition-colors"
          >
            <RotateCcw className="size-3.5 text-foreground" />
          </button>
        </div>
      )}
      <div
        className={
          'relative flex-1 overflow-auto p-4 bg-muted/20' +
          (zoomed ? '' : ' flex items-center justify-center')
        }
        onWheel={handleWheel}
      >
        {loading && (
          <div className="absolute inset-0 z-10">
            <PreviewLoading />
          </div>
        )}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={url}
          alt={alt}
          className="object-contain rounded-md"
          style={
            zoomed && naturalSize
              ? { width: naturalSize.w * scale, height: naturalSize.h * scale }
              : { maxWidth: '100%', maxHeight: '100%' }
          }
          onLoad={handleLoad}
          onError={() => {
            setLoading(false)
            setError(true)
          }}
        />
      </div>
    </div>
  )
}
