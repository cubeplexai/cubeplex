'use client'

import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'

export interface JsonViewProps {
  schema: unknown
}

export function JsonView({ schema }: JsonViewProps) {
  const t = useTranslations('mcp.tools.detail.json')
  const [copied, setCopied] = useState(false)
  const pretty = JSON.stringify(schema ?? {}, null, 2)

  async function handleCopy(): Promise<void> {
    await navigator.clipboard.writeText(pretty)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="relative overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border/60 bg-muted/40 px-3 py-2">
        <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          input_schema
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void handleCopy()}
          className="h-7"
        >
          {copied ? (
            <Check data-icon="inline-start" className="h-3.5 w-3.5" />
          ) : (
            <Copy data-icon="inline-start" className="h-3.5 w-3.5" />
          )}
          {copied ? t('copied') : t('copy')}
        </Button>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-xs leading-relaxed">{pretty}</pre>
    </div>
  )
}
