'use client'

import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface CopyIngestUrlProps {
  wsId: string
  triggerId: string
}

export function CopyIngestUrl({ wsId, triggerId }: CopyIngestUrlProps) {
  const [copied, setCopied] = useState(false)

  function getIngestUrl(): string {
    if (typeof window === 'undefined') return ''
    return `${window.location.origin}/api/v1/ws/${wsId}/triggers/${triggerId}/ingest`
  }

  async function handleCopy(): Promise<void> {
    const url = getIngestUrl()
    await navigator.clipboard.writeText(url)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <Button
      variant="outline"
      size="sm"
      className="gap-1.5"
      onClick={() => void handleCopy()}
      data-testid="copy-ingest-url"
    >
      {copied ? <Check className="size-3.5 text-success-fg" /> : <Copy className="size-3.5" />}
      {copied ? 'Copied!' : 'Copy ingest URL'}
    </Button>
  )
}
