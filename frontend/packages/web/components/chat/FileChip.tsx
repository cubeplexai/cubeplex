'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { X } from 'lucide-react'
import type { UploadingFile } from '@cubebox/core'
import { getFileVisual } from '@/lib/fileIcons'
import { cn } from '@/lib/utils'

interface Props {
  item: UploadingFile
  thumbnailUrl?: string | null
  onCancel: () => void
}

export function FileChip({ item, thumbnailUrl, onCancel }: Props): React.ReactElement {
  const t = useTranslations('chatExtras.uploadErrors')
  const [mounted, setMounted] = useState(false)
  useEffect(() => {
    requestAnimationFrame(() => setMounted(true))
  }, [])

  const visual = getFileVisual({ filename: item.filename })
  const isUploading = item.status === 'uploading'
  const isError = item.status === 'error'
  const errorLabel = isError ? uploadErrorLabel(t, item) : null
  const radius = 18
  const circumference = 2 * Math.PI * radius
  const offset = (1 - item.progress) * circumference

  return (
    <div
      className={cn(
        'group relative inline-flex items-center gap-2 rounded-lg border border-border bg-card pl-2 pr-2.5 py-1.5 text-xs transition-all duration-150 ease-out',
        mounted ? 'opacity-100 scale-100' : 'opacity-0 scale-[0.96]',
      )}
    >
      <div
        className={cn('relative size-10 shrink-0 rounded-md grid place-items-center', visual.bg)}
      >
        {thumbnailUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={thumbnailUrl}
            alt={item.filename}
            className="absolute inset-0 size-full rounded-md object-cover"
          />
        ) : (
          <visual.Icon className={cn('size-5', visual.fg)} />
        )}
        {(isUploading || isError) && (
          <svg className="absolute inset-0" viewBox="0 0 40 40" aria-hidden>
            <circle
              cx="20"
              cy="20"
              r={radius}
              fill="none"
              stroke="currentColor"
              className="text-white/25"
              strokeWidth="2"
            />
            <circle
              cx="20"
              cy="20"
              r={radius}
              fill="none"
              stroke="currentColor"
              className={isError ? 'text-danger-surface' : 'text-white'}
              strokeWidth="2"
              strokeDasharray={circumference}
              strokeDashoffset={offset}
              strokeLinecap="round"
              transform="rotate(-90 20 20)"
              style={{ transition: 'stroke-dashoffset 200ms ease-out' }}
            />
          </svg>
        )}
      </div>
      <div className="flex flex-col leading-tight max-w-[160px]">
        <span className="truncate font-medium" title={item.filename}>
          {item.filename}
        </span>
        <span
          className={cn(
            'text-[10px] truncate',
            isError ? 'text-destructive' : 'text-muted-foreground',
          )}
        >
          {errorLabel ?? visual.label}
        </span>
      </div>
      <button
        type="button"
        onClick={onCancel}
        className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full bg-foreground text-background hover:scale-110 transition-transform"
        aria-label={`Remove ${item.filename}`}
      >
        <X className="size-3" />
      </button>
    </div>
  )
}

function uploadErrorLabel(
  t: ReturnType<typeof useTranslations<'chatExtras.uploadErrors'>>,
  item: UploadingFile,
): string {
  if (item.errorCode === 'INVALID_MIME_TYPE') return t('invalidMimeType')
  if (item.errorCode === 'FILE_TOO_LARGE') return t('fileTooLarge')
  if (item.errorCode === 'QUOTA_EXCEEDED') return t('quotaExceeded')
  return item.error || t('generic')
}
