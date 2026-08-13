'use client'

import { memo, useState } from 'react'
import { useTranslations } from 'next-intl'

import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { getFileVisual } from '@/lib/fileIcons'
import { cn } from '@/lib/utils'

/** Shape returned in present_file tool results (subset used by the card). */
export interface PresentedFileMeta {
  id: string
  conversation_id: string
  filename: string
  mime_type: string
  size_bytes: number
  kind: string
  caption?: string | null
  width?: number | null
  height?: number | null
}

interface PresentedFileCardProps {
  /** Null while the tool result has not arrived. */
  file: PresentedFileMeta | null
  /** Fallback caption from tool args when result is still loading. */
  captionFallback?: string
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function presentedUrl(
  workspaceId: string,
  conversationId: string,
  fileId: string,
  kind: 'content' | 'thumbnail',
): string {
  return (
    `/api/v1/ws/${workspaceId}/conversations/${conversationId}` +
    `/presented-files/${fileId}/${kind}`
  )
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

function PresentedFileCardImpl({ file, captionFallback }: PresentedFileCardProps) {
  const t = useTranslations('chatExtras')
  const { workspaceId } = useWorkspaceContext()
  const [imgLoaded, setImgLoaded] = useState(false)
  /** Prefer thumbnail; fall back to full content once if thumb fails. */
  const [useFullContent, setUseFullContent] = useState(false)
  const [imgFailed, setImgFailed] = useState(false)

  if (!file) {
    return (
      <div className="my-2 w-full overflow-hidden rounded border border-border bg-card">
        <div className="relative aspect-[4/3] bg-muted/30">
          <Shimmer />
          <div className="absolute inset-x-0 bottom-3 flex justify-center">
            <span className="rounded-full bg-background/70 px-3 py-1 text-xs text-muted-foreground backdrop-blur-sm">
              {t('presentFileLoading')}
            </span>
          </div>
        </div>
      </div>
    )
  }

  const caption = file.caption || captionFallback || file.filename
  if (!workspaceId) {
    return (
      <div className="my-2 rounded border border-border bg-card px-3 py-2 text-xs text-muted-foreground">
        {caption}
      </div>
    )
  }
  const fullUrl = presentedUrl(workspaceId, file.conversation_id, file.id, 'content')
  const thumbUrl = presentedUrl(workspaceId, file.conversation_id, file.id, 'thumbnail')
  const displayUrl = useFullContent ? fullUrl : thumbUrl
  const isImage = file.kind === 'image' || file.mime_type.startsWith('image/')

  if (isImage && !imgFailed) {
    return (
      <a
        href={fullUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="my-2 block w-full overflow-hidden rounded border border-border bg-card
          transition-colors hover:border-primary/30"
      >
        <div className="relative bg-muted/30">
          {!imgLoaded && (
            <div className="aspect-[4/3]">
              <Shimmer />
            </div>
          )}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={displayUrl}
            alt={caption}
            className={cn(
              'w-full h-auto transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0 absolute inset-0',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={() => {
              if (!useFullContent) {
                // Thumbnail missing/corrupt → retry once with full content.
                setUseFullContent(true)
                setImgLoaded(false)
                return
              }
              setImgFailed(true)
            }}
          />
        </div>
        {caption && (
          <div className="border-t border-border px-3 py-2">
            <p className="line-clamp-2 text-xs text-muted-foreground">{caption}</p>
          </div>
        )}
      </a>
    )
  }

  const visual = getFileVisual({ filename: file.filename, mime_type: file.mime_type })

  return (
    <a
      href={fullUrl}
      download={file.filename}
      className="my-2 inline-flex items-center gap-2 rounded-lg border border-border bg-card
        px-2 py-1.5 text-[11px] hover:bg-muted/40 transition-colors"
    >
      <div className={cn('size-9 shrink-0 rounded-md grid place-items-center', visual.bg)}>
        <visual.Icon className={cn('size-4', visual.fg)} />
      </div>
      <div className="flex flex-col leading-tight max-w-[200px]">
        <span className="truncate font-medium" title={file.filename}>
          {file.filename}
        </span>
        <span className="text-muted-foreground">{formatBytes(file.size_bytes)}</span>
        {caption && caption !== file.filename && (
          <span className="truncate text-muted-foreground" title={caption}>
            {caption}
          </span>
        )}
      </div>
    </a>
  )
}

export const PresentedFileCard = memo(PresentedFileCardImpl)
