'use client'

import { useEffect, useState, useCallback } from 'react'
import { Upload } from 'lucide-react'
import { useAttachmentStore, createApiClient } from '@cubeplex/core'
import { useTranslations } from 'next-intl'

import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

interface Props {
  conversationId: string
}

export function UploadDropzone({ conversationId }: Props) {
  const t = useTranslations('chatExtras')
  const [active, setActive] = useState(false)
  const upload = useAttachmentStore((s) => s.upload)
  const { workspaceId } = useWorkspaceContext()

  const handleDrop = useCallback(
    async (e: DragEvent) => {
      e.preventDefault()
      setActive(false)
      const files = Array.from(e.dataTransfer?.files || [])
      if (!files.length) return
      const client = createApiClient('')
      if (workspaceId) client.setWorkspaceId(workspaceId)
      await upload(client, conversationId, files)
    },
    [conversationId, upload, workspaceId],
  )

  useEffect(() => {
    let counter = 0
    const onEnter = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes('Files')) return
      counter++
      setActive(true)
    }
    const onLeave = () => {
      counter--
      if (counter <= 0) {
        counter = 0
        setActive(false)
      }
    }
    const onOver = (e: DragEvent) => {
      e.preventDefault()
    }
    window.addEventListener('dragenter', onEnter)
    window.addEventListener('dragleave', onLeave)
    window.addEventListener('dragover', onOver)
    window.addEventListener('drop', handleDrop)
    return () => {
      window.removeEventListener('dragenter', onEnter)
      window.removeEventListener('dragleave', onLeave)
      window.removeEventListener('dragover', onOver)
      window.removeEventListener('drop', handleDrop)
    }
  }, [handleDrop])

  if (!active) return null
  return (
    <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-background/60 backdrop-blur-sm">
      <div className="rounded-2xl border-2 border-dashed border-primary/60 bg-card px-12 py-10 text-center shadow-lg">
        <Upload className="mx-auto mb-3 size-10 text-primary" />
        <p className="text-base font-medium">{t('uploadDropzone')}</p>
        <p className="mt-1 text-xs text-muted-foreground">{t('uploadFormats')}</p>
      </div>
    </div>
  )
}
