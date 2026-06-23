'use client'

import { FileAudio } from 'lucide-react'

interface MediaPlayerProps {
  url: string
  type: 'audio' | 'video'
  filename: string
}

export function MediaPlayer({ url, type, filename }: MediaPlayerProps) {
  if (type === 'audio') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-6 p-8">
        <div className="flex size-20 items-center justify-center rounded-2xl bg-muted">
          <FileAudio className="size-10 text-muted-foreground" />
        </div>
        <p className="text-sm font-medium text-foreground">{filename}</p>
        <audio controls preload="metadata" className="w-full max-w-md">
          <source src={url} />
        </audio>
      </div>
    )
  }

  return (
    <div className="flex h-full items-center justify-center bg-black/95 p-2">
      <video
        controls
        preload="metadata"
        className="max-w-full max-h-full rounded"
      >
        <source src={url} />
      </video>
    </div>
  )
}
