'use client'

import { usePanelStore } from '@cubeplex/core'
import { getFileVisual } from '@/lib/fileIcons'
import { cn } from '@/lib/utils'

export interface MessageFileChipProps {
  attachmentId: string
  filename: string
  mimeType: string
  sizeBytes: number
  downloadUrl: string
  onOpenImage?: (downloadUrl: string, filename: string) => void
}

const PANEL_FAMILIES = new Set(['pdf', 'markdown', 'text', 'code', 'json', 'csv', 'video', 'audio'])

export function MessageFileChip({
  attachmentId,
  filename,
  mimeType,
  sizeBytes,
  downloadUrl,
  onOpenImage,
}: MessageFileChipProps): React.ReactElement {
  const openAttachment = usePanelStore((s) => s.openAttachment)
  const visual = getFileVisual({ filename, mime_type: mimeType })

  const handleClick: React.MouseEventHandler = (e) => {
    if (visual.family === 'image' && onOpenImage) {
      e.preventDefault()
      onOpenImage(downloadUrl, filename)
      return
    }
    if (PANEL_FAMILIES.has(visual.family)) {
      e.preventDefault()
      openAttachment({ attachmentId, filename, downloadUrl, mimeType, sizeBytes })
      return
    }
    // word/excel/ppt/archive/unknown → let the <a> default download behavior run
  }

  return (
    <a
      href={downloadUrl}
      download
      onClick={handleClick}
      className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-2 py-1.5 text-[11px] hover:bg-muted/40 transition-colors"
    >
      <div className={cn('size-9 shrink-0 rounded-md grid place-items-center', visual.bg)}>
        <visual.Icon className={cn('size-4', visual.fg)} />
      </div>
      <div className="flex flex-col leading-tight max-w-[140px]">
        <span className="truncate font-medium" title={filename}>
          {filename}
        </span>
        <span className="text-[10px] text-muted-foreground truncate">{visual.label}</span>
      </div>
    </a>
  )
}
