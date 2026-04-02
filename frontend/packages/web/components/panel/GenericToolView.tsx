'use client'

import { useState } from 'react'
import { Copy, Check } from 'lucide-react'

interface GenericToolViewProps {
  args: Record<string, unknown>
  result: string | null
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      onClick={handleCopy}
      className="p-1 rounded hover:bg-muted/50
        transition-colors"
      title="Copy"
    >
      {copied ? (
        <Check className="size-3 text-emerald-500" />
      ) : (
        <Copy
          className="size-3 text-muted-foreground"
        />
      )}
    </button>
  )
}

function formatContent(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

export function GenericToolView({
  args,
  result,
}: GenericToolViewProps) {
  const requestText = JSON.stringify(args, null, 2)
  const responseText = result
    ? formatContent(result)
    : null

  return (
    <div className="p-4 space-y-4">
      <div>
        <div
          className="flex items-center
            justify-between mb-2"
        >
          <span
            className="text-xs font-medium
              text-muted-foreground uppercase
              tracking-wider"
          >
            Request
          </span>
          <CopyButton text={requestText} />
        </div>
        <div className="bg-muted rounded-lg p-3">
          <pre
            className="font-mono text-sm text-foreground
              whitespace-pre-wrap break-all"
          >
            {requestText}
          </pre>
        </div>
      </div>
      {responseText && (
        <div>
          <div
            className="flex items-center
              justify-between mb-2"
          >
            <span
              className="text-xs font-medium
                text-muted-foreground uppercase
                tracking-wider"
            >
              Response
            </span>
            <CopyButton text={responseText} />
          </div>
          <div className="bg-muted rounded-lg p-3">
            <pre
              className="font-mono text-sm
                text-foreground whitespace-pre-wrap
                break-all"
            >
              {responseText}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
