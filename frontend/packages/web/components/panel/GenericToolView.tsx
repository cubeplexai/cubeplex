'use client'

import { useState, useEffect, useRef } from 'react'
import { Copy, Check } from 'lucide-react'

interface GenericToolViewProps {
  args: Record<string, unknown>
  result: string | null
  highlightText?: string | null
  highlightKey?: number
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
  highlightText,
  highlightKey,
}: GenericToolViewProps) {
  const responseRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (!highlightText || !responseRef.current) return
    const text = responseRef.current.textContent ?? ''
    const searchText = highlightText.slice(0, 50)
    let timer: ReturnType<typeof setTimeout> | undefined
    if (text.includes(searchText)) {
      responseRef.current.classList.add('ring-2', 'ring-yellow-400/50', 'bg-yellow-50/10')
      responseRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
      timer = setTimeout(() => {
        responseRef.current?.classList.remove(
          'ring-2', 'ring-yellow-400/50', 'bg-yellow-50/10',
        )
      }, 2000)
    }
    return () => { if (timer) clearTimeout(timer) }
  }, [highlightText, highlightKey])

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
              ref={responseRef}
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
