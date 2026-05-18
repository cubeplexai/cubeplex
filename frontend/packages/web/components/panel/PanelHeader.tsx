'use client'

import { useState } from 'react'
import { X, Copy, Check, Plug } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { getToolIcon, getParamSummary } from '@/lib/toolIcons'
import { useMcpToolRegistryStore } from '@cubebox/core'

interface PanelHeaderProps {
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
  onClose: () => void
}

export function PanelHeader({ toolName, toolArgs, toolResult, onClose }: PanelHeaderProps) {
  const t = useTranslations('panel.header')
  const [copied, setCopied] = useState(false)
  const mcpEntry = useMcpToolRegistryStore((s) => s.lookup(toolName))
  const displayName = mcpEntry?.bare_name ?? toolName
  const mcpIconSrc = mcpEntry
    ? (mcpEntry.tool_icons[0]?.src ?? mcpEntry.server_icons[0]?.src ?? null)
    : null
  const FallbackIcon = getToolIcon(displayName)
  const summary = getParamSummary(displayName, toolArgs, 40)
  const tooltip = mcpEntry ? `${mcpEntry.server_name} · ${mcpEntry.bare_name}` : displayName

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
      {mcpIconSrc ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={mcpIconSrc} alt="" className="size-3.5 rounded-sm shrink-0 object-contain" />
      ) : mcpEntry ? (
        <Plug className="size-3.5 text-muted-foreground shrink-0" />
      ) : (
        /* eslint-disable-next-line react-hooks/static-components */
        <FallbackIcon className="size-3.5 text-muted-foreground shrink-0" />
      )}
      <span
        className="text-sm font-medium text-foreground
          shrink-0"
        title={tooltip}
      >
        {displayName}
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
          title={t('copy')}
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
          title={t('close')}
        >
          <X className="size-3.5 text-muted-foreground" />
        </button>
      </span>
    </header>
  )
}
