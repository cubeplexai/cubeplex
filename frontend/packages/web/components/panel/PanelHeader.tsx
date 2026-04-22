'use client'

import { useState } from 'react'
import { X, Copy, Check } from 'lucide-react'
import { getToolIcon, getParamSummary } from '@/lib/toolIcons'

interface PanelHeaderProps {
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
  onClose: () => void
}

export function PanelHeader({ toolName, toolArgs, toolResult, onClose }: PanelHeaderProps) {
  const [copied, setCopied] = useState(false)
  const Icon = getToolIcon(toolName)
  const summary = getParamSummary(toolName, toolArgs, 40)

  const handleCopy = async () => {
    const text = toolResult ?? JSON.stringify(toolArgs, null, 2)
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <header
      className="h-11 border-b border-border flex
        items-center gap-2 px-4 shrink-0 bg-card"
    >
      <Icon
        className="size-3.5 text-muted-foreground
          shrink-0"
      />
      <span
        className="text-sm font-medium text-foreground
          shrink-0"
      >
        {toolName}
      </span>
      {summary && (
        <span
          className="text-xs text-muted-foreground
            truncate"
        >
          {summary}
        </span>
      )}
      <span className="ml-auto flex items-center gap-1">
        <button
          onClick={handleCopy}
          className="p-1 rounded hover:bg-muted/50
            transition-colors"
          title="Copy"
        >
          {copied ? (
            <Check className="size-3.5 text-emerald-500" />
          ) : (
            <Copy
              className="size-3.5
                text-muted-foreground"
            />
          )}
        </button>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-muted/50
            transition-colors"
          title="Close"
        >
          <X className="size-3.5 text-muted-foreground" />
        </button>
      </span>
    </header>
  )
}
