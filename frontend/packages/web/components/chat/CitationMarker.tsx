'use client'

import { useState, useCallback } from 'react'
import { Globe, ExternalLink, Calendar } from 'lucide-react'
import { useCitationStore, usePanelStore, useMessageStore } from '@cubebox/core'
import type { CitationData } from '@cubebox/core'
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from '@/components/ui/popover'

interface CitationMarkerProps {
  citationId: number
  chunkIndex: number
  conversationId: string
}

function getFaviconUrl(domain: string): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=16`
}

function CitationHoverContent({
  citation,
  chunkIndex,
  onOpenPanel,
}: {
  citation: CitationData
  chunkIndex: number
  onOpenPanel: () => void
}) {
  const { metadata, chunks } = citation
  const chunk = chunks.find((c) => c.chunk_index === chunkIndex)
  const [faviconError, setFaviconError] = useState(false)
  const isWeb = metadata.source_type === 'web'

  return (
    <div className="flex flex-col gap-2">
      {/* Header: favicon + domain + source type */}
      <div className="flex items-center gap-1.5">
        {isWeb && metadata.domain && !faviconError ? (
          <img
            src={getFaviconUrl(metadata.domain)}
            alt=""
            className="size-4 rounded-sm shrink-0"
            onError={() => setFaviconError(true)}
          />
        ) : (
          <Globe className="size-4 text-muted-foreground shrink-0" />
        )}
        <span className="text-xs text-muted-foreground truncate">
          {metadata.domain || metadata.source_type}
        </span>
        <span
          className="ml-auto text-[10px] font-medium text-muted-foreground/60
          bg-muted px-1.5 py-0.5 rounded shrink-0"
        >
          {metadata.source_type}
        </span>
      </div>

      {/* Title */}
      {metadata.title && (
        <button
          type="button"
          onClick={onOpenPanel}
          className="text-sm font-medium text-foreground hover:text-primary
            transition-colors text-left line-clamp-2 cursor-pointer"
        >
          {metadata.title}
        </button>
      )}

      {/* Chunk snippet */}
      {chunk && (
        <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3">
          {chunk.content}
        </p>
      )}

      {/* Footer: date + URL */}
      <div className="flex items-center gap-2 text-[10px] text-muted-foreground/60">
        {metadata.published_at && (
          <span className="flex items-center gap-1">
            <Calendar className="size-2.5" />
            {metadata.published_at}
          </span>
        )}
        {isWeb && metadata.url && (
          <a
            href={metadata.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-primary hover:underline truncate ml-auto"
          >
            <ExternalLink className="size-2.5 shrink-0" />
            <span className="truncate">{metadata.url}</span>
          </a>
        )}
      </div>
    </div>
  )
}

export function CitationMarker({
  citationId,
  chunkIndex,
  conversationId,
}: CitationMarkerProps) {
  const citation = useCitationStore(
    (s) => s.citations[conversationId]?.[citationId],
  )
  const openTool = usePanelStore((s) => s.openTool)

  const handleOpenPanel = useCallback(() => {
    if (!citation) return
    const chunk = citation.chunks.find((c) => c.chunk_index === chunkIndex)
    const state = useMessageStore.getState()

    // Try streaming toolResultMap first, then fall back to history messages
    let toolName = 'web_search'
    let content: string | null = null
    let contentType: string | undefined

    const streamResult = state.toolResultMap[citation.tool_call_id]
    if (streamResult) {
      content = streamResult.content
      contentType = streamResult.contentType
    } else {
      // Find tool result from history messages (top-level or inside subagent_events)
      outer: for (const msgs of Object.values(state.messages)) {
        for (const m of msgs) {
          if (m.role === 'tool' && m.tool_call_id === citation.tool_call_id) {
            toolName = m.name ?? 'web_search'
            content = m.content
            break outer
          }
          // Search subagent inner tool results
          if (m.role === 'tool' && m.name === 'subagent' && m.subagent_events?.tool_results) {
            const inner = m.subagent_events.tool_results.find(
              (tr) => tr.tool_call_id === citation.tool_call_id,
            )
            if (inner) {
              toolName = inner.tool_name || 'web_search'
              content = inner.content
              contentType = inner.content_type ?? undefined
              break outer
            }
          }
        }
      }
    }

    if (content) {
      openTool(toolName, {}, content, contentType, undefined, chunk?.content)
    }
  }, [citation, chunkIndex, openTool])

  // No citation data available — render marker as plain text
  if (!citation) {
    return (
      <span className="text-muted-foreground/50 text-xs">
        【{citationId}-{chunkIndex}】
      </span>
    )
  }

  return (
    <Popover>
      <PopoverTrigger
        openOnHover
        delay={200}
        closeDelay={200}
        onClick={handleOpenPanel}
        className="inline-flex items-center justify-center min-w-[1.5em] h-[1.2em]
          px-1 mx-0.5 text-[10px] font-mono font-medium leading-none
          bg-primary/10 text-primary hover:bg-primary/20 rounded-full
          cursor-pointer transition-colors align-super relative -top-[1px]"
      >
        {citationId}.{chunkIndex}
      </PopoverTrigger>
      <PopoverContent side="top" sideOffset={4} className="w-80 p-3">
        <CitationHoverContent
          citation={citation}
          chunkIndex={chunkIndex}
          onOpenPanel={handleOpenPanel}
        />
      </PopoverContent>
    </Popover>
  )
}
