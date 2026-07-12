'use client'

import { useState, type ReactNode } from 'react'
import { X, Copy, Check, Plug, Maximize2, Minimize2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { getToolIcon, getParamSummary } from '@/lib/toolIcons'
import { useMcpToolRegistryStore } from '@cubeplex/core'

interface ToolHeaderSource {
  kind: 'tool'
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
}

interface PlainHeaderSource {
  kind: 'plain'
  icon: ReactNode
  title: string
  /** mono subtitle: command, path, url … */
  subtitle?: string
  copyText?: string
}

export type PanelHeaderSource = ToolHeaderSource | PlainHeaderSource

interface PanelHeaderProps {
  source: PanelHeaderSource
  /** per-adapter extras (version popover, download, take-over…) rendered before the standard actions */
  actions?: ReactNode
  fullscreen?: { active: boolean; onToggle: () => void }
  onClose: () => void
}

export function PanelHeader({ source, actions, fullscreen, onClose }: PanelHeaderProps) {
  const t = useTranslations('panel.header')
  const [copied, setCopied] = useState(false)
  const mcpEntry = useMcpToolRegistryStore((s) =>
    source.kind === 'tool' ? s.lookup(source.toolName) : null,
  )

  let icon: ReactNode
  let title: string
  let subtitle: string | undefined
  let copyText: string | undefined

  if (source.kind === 'tool') {
    const displayName = mcpEntry?.bare_name ?? source.toolName
    const pickSrc = (icons: { src: string; cached_src?: string | null }[]) => {
      for (const i of icons) {
        if (i.cached_src) return i.cached_src
        if (i.src.startsWith('data:image/') || i.src.startsWith('/')) return i.src
        if (i.src.startsWith('https://') || i.src.startsWith('http://')) return i.src
      }
      return null
    }
    const mcpIconSrc = mcpEntry
      ? (pickSrc(mcpEntry.tool_icons) ?? pickSrc(mcpEntry.server_icons))
      : null
    const FallbackIcon = getToolIcon(displayName)
    icon = mcpIconSrc ? (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={mcpIconSrc} alt="" className="size-3.5 rounded-xs shrink-0 object-contain" />
    ) : mcpEntry ? (
      <Plug className="size-3.5 text-muted-foreground shrink-0" />
    ) : (
      /* eslint-disable-next-line react-hooks/static-components */
      <FallbackIcon className="size-3.5 text-muted-foreground shrink-0" />
    )
    title = displayName
    subtitle = getParamSummary(displayName, source.toolArgs, 40) || undefined
    copyText = source.toolResult ?? JSON.stringify(source.toolArgs, null, 2)
  } else {
    icon = source.icon
    title = source.title
    subtitle = source.subtitle
    copyText = source.copyText
  }

  const handleCopy = async () => {
    if (copyText === undefined) return
    await navigator.clipboard.writeText(copyText)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <header className="h-11 border-b border-border flex items-center gap-2 px-4 shrink-0 bg-card">
      {icon}
      <span
        className="text-sm font-medium text-foreground min-w-[6ch] flex-1 truncate"
        title={title}
      >
        {title}
      </span>
      {subtitle && (
        <span className="font-mono text-xs text-muted-foreground truncate shrink min-w-0">
          {subtitle}
        </span>
      )}
      <span className="flex items-center gap-1 shrink-0">
        {actions}
        {copyText !== undefined && (
          <button
            onClick={handleCopy}
            className="p-1 rounded-xs hover:bg-accent transition-colors duration-fast"
            title={t('copy')}
          >
            {copied ? (
              <Check className="size-3.5 text-success-fg" />
            ) : (
              <Copy className="size-3.5 text-muted-foreground" />
            )}
          </button>
        )}
        {fullscreen && (
          <button
            onClick={fullscreen.onToggle}
            className="p-1 rounded-xs hover:bg-accent transition-colors duration-fast"
            title={t(fullscreen.active ? 'exitFullscreen' : 'fullscreen')}
          >
            {fullscreen.active ? (
              <Minimize2 className="size-3.5 text-muted-foreground" />
            ) : (
              <Maximize2 className="size-3.5 text-muted-foreground" />
            )}
          </button>
        )}
        <button
          onClick={onClose}
          className="p-1 rounded-xs hover:bg-accent transition-colors duration-fast"
          title={t('close')}
        >
          <X className="size-3.5 text-muted-foreground" />
        </button>
      </span>
    </header>
  )
}
