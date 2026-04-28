'use client'

import { X, FileText, ImageIcon, Loader2 } from 'lucide-react'
import type { UploadingFile } from '@cubebox/core'

interface Props {
  item: UploadingFile
  thumbnailUrl?: string | null
  onRemove: () => void
}

export function AttachmentChip({ item, thumbnailUrl, onRemove }: Props) {
  const isImage = thumbnailUrl != null
  const isUploading = item.status === 'uploading'
  const isError = item.status === 'error'

  return (
    <div className="relative inline-flex items-center gap-2 rounded-md border border-border bg-card px-2 py-1.5 text-xs">
      {isImage && thumbnailUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={thumbnailUrl} alt={item.filename} className="size-7 rounded object-cover" />
      ) : (
        <div className="size-7 grid place-items-center rounded bg-muted">
          {isImage ? <ImageIcon className="size-4" /> : <FileText className="size-4" />}
        </div>
      )}
      <div className="flex flex-col leading-tight">
        <span className="max-w-[140px] truncate font-medium">{item.filename}</span>
        <span className={`text-[10px] ${isError ? 'text-destructive' : 'text-muted-foreground'}`}>
          {isUploading
            ? `${Math.round(item.progress * 100)}%`
            : isError
              ? 'failed'
              : `${(item.size / 1024).toFixed(0)}KB`}
        </span>
      </div>
      {isUploading && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
      <button
        type="button"
        onClick={onRemove}
        className="ml-1 grid size-5 place-items-center rounded hover:bg-muted"
        aria-label={`Remove ${item.filename}`}
      >
        <X className="size-3" />
      </button>
    </div>
  )
}
