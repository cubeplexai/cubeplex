'use client'

import { useState } from 'react'
import { Check, Copy } from 'lucide-react'

interface Props {
  value: string | undefined | null
  language?: 'json' | 'text'
}

export function JsonBlock({ value, language = 'json' }: Props) {
  const [copied, setCopied] = useState(false)
  if (!value) return null
  const formatted = (() => {
    if (language !== 'json') return value
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  })()
  const copy = async () => {
    await navigator.clipboard.writeText(formatted)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div className="relative">
      <button
        type="button"
        onClick={copy}
        className="absolute right-2 top-2 rounded bg-card/80 p-1 text-muted-foreground hover:text-foreground"
        aria-label="Copy"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
      <pre className="max-h-96 overflow-auto rounded bg-muted/40 p-3 text-xs font-mono text-foreground">
        {formatted}
      </pre>
    </div>
  )
}
