'use client'

import { useState } from 'react'
import { FileText, Download } from 'lucide-react'
import { ImageLightbox } from './ImageLightbox'

export interface MessageAttachmentDto {
  id: string
  filename: string
  kind: 'image' | 'document' | 'other'
  size_bytes: number
  width?: number | null
  height?: number | null
  thumbnail_url?: string | null
  download_url: string
}

interface Props {
  attachments: MessageAttachmentDto[]
}

function formatSize(n: number): string {
  if (n < 1024) return `${n}B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`
  return `${(n / (1024 * 1024)).toFixed(1)}MB`
}

export function MessageAttachments({ attachments }: Props) {
  const [openSrc, setOpenSrc] = useState<{ src: string; alt: string } | null>(null)
  if (!attachments?.length) return null

  return (
    <div className="mt-2 flex flex-wrap gap-2" data-testid="message-attachments">
      {attachments.map((a) => {
        if (a.kind === 'image' && a.thumbnail_url) {
          return (
            <button
              key={a.id}
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
          <a
            key={a.id}
            href={a.download_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs hover:bg-muted"
          >
            <FileText className="size-4 text-muted-foreground" />
            <span className="max-w-[180px] truncate">{a.filename}</span>
            <span className="text-muted-foreground">{formatSize(a.size_bytes)}</span>
            <Download className="size-3.5 text-muted-foreground" />
          </a>
        )
      })}
      {openSrc && (
        <ImageLightbox src={openSrc.src} alt={openSrc.alt} onClose={() => setOpenSrc(null)} />
      )}
    </div>
  )
}
