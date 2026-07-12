'use client'

import { useState, useMemo } from 'react'
import { createApiClient } from '@cubeplex/core'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { ImageLightbox } from './ImageLightbox'
import { MessageFileChip } from './MessageFileChip'

export interface MessageAttachmentDto {
  file_id: string
  filename: string
  kind: 'image' | 'document' | 'other'
  mime_type?: string
  size_bytes: number
  width?: number | null
  height?: number | null
  thumbnail_url?: string | null
  download_url?: string | null
}

interface Props {
  attachments: MessageAttachmentDto[]
  conversationId: string
}

export function MessageAttachments({
  attachments,
  conversationId,
}: Props): React.ReactElement | null {
  const { workspaceId } = useWorkspaceContext()
  const [openSrc, setOpenSrc] = useState<{ src: string; alt: string } | null>(null)

  const resolved = useMemo(() => {
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    const baseApi = `/api/v1/conversations/${conversationId}/attachments`
    const fix = (url: string | null | undefined): string => {
      if (!url) return ''
      if (url.startsWith('./attachments/')) {
        const tail = url.slice('./attachments/'.length)
        return client.resolvePath(`${baseApi}/${tail}`)
      }
      return url
    }
    return attachments.map((a) => ({
      ...a,
      thumbnail_url: a.thumbnail_url ? fix(a.thumbnail_url) : null,
      // /attachments/{id} is the metadata endpoint; bytes live under /content.
      // Historical messages reloaded from cubepi only carry file_id, so we
      // build the URL ourselves when download_url isn't pre-filled.
      download_url: fix(a.download_url ?? `./attachments/${a.file_id}/content`),
    }))
  }, [attachments, conversationId, workspaceId])

  if (!resolved.length) return null

  return (
    <div
      className="flex flex-wrap gap-1.5 justify-end max-w-[72%] ml-auto mb-1.5"
      data-testid="message-attachments"
    >
      {resolved.map((a) => {
        if (a.kind === 'image' && a.thumbnail_url) {
          return (
            <button
              key={a.file_id}
              type="button"
              onClick={() => setOpenSrc({ src: a.download_url, alt: a.filename })}
              className="group relative overflow-hidden rounded-lg border border-border"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={a.thumbnail_url}
                alt={a.filename}
                className="size-24 object-cover transition group-hover:scale-105"
              />
              <span className="absolute bottom-0 left-0 right-0 truncate bg-background/80 px-1 py-0.5 text-[10px]">
                {a.filename}
              </span>
            </button>
          )
        }
        return (
          <MessageFileChip
            key={a.file_id}
            attachmentId={a.file_id}
            filename={a.filename}
            mimeType={a.mime_type ?? ''}
            sizeBytes={a.size_bytes}
            downloadUrl={a.download_url}
            onOpenImage={(src, alt) => setOpenSrc({ src, alt })}
          />
        )
      })}
      {openSrc && (
        <ImageLightbox src={openSrc.src} alt={openSrc.alt} onClose={() => setOpenSrc(null)} />
      )}
    </div>
  )
}
