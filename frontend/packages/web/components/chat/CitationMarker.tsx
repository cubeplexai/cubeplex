'use client'

import { useState, useCallback, useRef, useEffect } from 'react'
import { Globe, Calendar } from 'lucide-react'
import {
  useCitationStore,
  usePanelStore,
  useMessageStore,
  bareToolName,
  getSubagentSummary,
  getToolResultPreviewContent,
} from '@cubeplex/core'
import type { CitationData } from '@cubeplex/core'
import { useTranslations } from 'next-intl'

import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { getFileVisual } from '@/lib/fileIcons'

interface CitationMarkerProps {
  citationId: number
  chunkIndex: number
  conversationId: string
}

function getFaviconUrl(url: string): string {
  try {
    return `${new URL(url).origin}/favicon.ico`
  } catch {
    return ''
  }
}

function basename(path?: string): string {
  if (!path) return ''
  const i = path.lastIndexOf('/')
  return i >= 0 ? path.slice(i + 1) : path
}

/**
 * Splits a namespaced MCP tool name (e.g. "webtools__web_search") into its
 * display portion ("web_search") and optional server prefix ("webtools").
 * Non-namespaced names are returned as-is with server = null.
 * The display half delegates to bareToolName for consistent stripping.
 */
export function splitNamespacedToolName(name: string): { display: string; server: string | null } {
  const idx = name.indexOf('__')
  if (idx < 0) return { display: bareToolName(name), server: null }
  return { display: bareToolName(name), server: name.slice(0, idx) }
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
  if (citation.metadata.source_type === 'file') {
    return (
      <FileSourceHoverContent
        citation={citation}
        chunkIndex={chunkIndex}
        onOpenPanel={onOpenPanel}
      />
    )
  }
  return (
    <WebSourceHoverContent citation={citation} chunkIndex={chunkIndex} onOpenPanel={onOpenPanel} />
  )
}

function WebSourceHoverContent({
  citation,
  chunkIndex,
  onOpenPanel,
}: {
  citation: CitationData
  chunkIndex: number
  onOpenPanel: () => void
}) {
  const { metadata, chunks } = citation
  const sortedChunks = [...chunks].sort((a, b) => a.chunk_index - b.chunk_index)
  const [faviconError, setFaviconError] = useState(false)
  const isWeb = metadata.source_type === 'web'
  const activeRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (activeRef.current && scrollRef.current) {
      const container = scrollRef.current
      const el = activeRef.current
      const top = el.offsetTop - container.offsetTop
      container.scrollTop = top - 8
    }
  }, [])

  return (
    <div className="flex flex-col gap-2">
      {/* Title */}
      {metadata.title && (
        <button
          type="button"
          onClick={onOpenPanel}
          className="text-sm font-semibold text-foreground hover:text-primary
            transition-colors text-left line-clamp-2 cursor-pointer"
        >
          {metadata.title}
        </button>
      )}

      {/* Source: favicon + URL + date + badge */}
      <div className="flex items-center gap-1.5">
        {isWeb && metadata.url && !faviconError ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={getFaviconUrl(metadata.url)}
            alt=""
            className="size-3.5 rounded-sm shrink-0"
            onError={() => setFaviconError(true)}
          />
        ) : (
          <Globe className="size-3.5 text-muted-foreground shrink-0" />
        )}
        {isWeb && metadata.url ? (
          <a
            href={metadata.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] text-muted-foreground hover:text-primary
              truncate transition-colors"
          >
            {metadata.domain || metadata.url}
          </a>
        ) : (
          <span className="text-[11px] text-muted-foreground truncate">
            {metadata.domain || metadata.source_type}
          </span>
        )}
        {metadata.published_at && (
          <>
            <span className="text-muted-foreground/30">·</span>
            <span
              className="flex items-center gap-0.5 text-[10px] text-muted-foreground/60
              shrink-0"
            >
              <Calendar className="size-2.5" />
              {metadata.published_at}
            </span>
          </>
        )}
        {(() => {
          const { display: stDisplay, server: stServer } = splitNamespacedToolName(
            metadata.source_type,
          )
          return (
            <span
              className="ml-auto text-[10px] font-medium text-muted-foreground/60
                bg-muted px-1.5 py-0.5 rounded shrink-0"
              title={stServer ? `${stDisplay} from ${stServer}` : stDisplay}
            >
              {stServer ? `${stDisplay} (${stServer})` : stDisplay}
            </span>
          )
        })()}
      </div>

      {/* Chunk list with active highlight */}
      {sortedChunks.length > 0 && (
        <div ref={scrollRef} className="max-h-40 overflow-y-auto -mx-1 px-1">
          <div className="flex flex-col gap-0.5">
            {sortedChunks.map((c) => {
              const isActive = c.chunk_index === chunkIndex
              return (
                <div
                  key={c.chunk_index}
                  ref={isActive ? activeRef : undefined}
                  className={`text-xs leading-relaxed rounded px-2 py-1 ${
                    isActive
                      ? 'bg-accent text-foreground border-l-2 border-primary'
                      : 'text-muted-foreground/50'
                  }`}
                >
                  <span className={isActive ? '' : 'line-clamp-2'}>{c.content}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function FileSourceHoverContent({
  citation,
  chunkIndex,
  onOpenPanel,
}: {
  citation: CitationData
  chunkIndex: number
  onOpenPanel: () => void
}) {
  const t = useTranslations('chatExtras')
  const { metadata, chunks } = citation
  const sortedChunks = [...chunks].sort((a, b) => a.chunk_index - b.chunk_index)
  const visual = getFileVisual({ filename: basename(metadata.path), mime_type: metadata.mime })
  const activeRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (activeRef.current && scrollRef.current) {
      const container = scrollRef.current
      const el = activeRef.current
      const top = el.offsetTop - container.offsetTop
      container.scrollTop = top - 8
    }
  }, [])

  const range = metadata.page_range
    ? `Pages ${metadata.page_range}`
    : metadata.line_range
      ? `Lines ${metadata.line_range}`
      : null

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={onOpenPanel}
        className="flex items-center gap-2 text-left text-sm font-semibold text-foreground hover:text-primary transition-colors cursor-pointer"
      >
        <span className={`size-6 grid place-items-center rounded ${visual.bg}`}>
          <visual.Icon className={`size-3 ${visual.fg}`} />
        </span>
        <span className="truncate">{basename(metadata.path) || '(file)'}</span>
      </button>
      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
        <span className="truncate" title={metadata.path}>
          {metadata.path}
        </span>
        {range && <span className="rounded bg-muted px-1.5 py-0.5 font-medium">{range}</span>}
        {metadata.truncated && (
          <span className="rounded bg-warning-surface px-1.5 py-0.5 font-medium text-warning-fg">
            {t('citationTruncated')}
          </span>
        )}
        <span className="ml-auto rounded bg-muted px-1.5 py-0.5 font-medium">file</span>
      </div>
      {sortedChunks.length > 0 && (
        <div ref={scrollRef} className="max-h-40 overflow-y-auto -mx-1 px-1">
          <div className="flex flex-col gap-0.5">
            {sortedChunks.map((c) => {
              const isActive = c.chunk_index === chunkIndex
              return (
                <div
                  key={c.chunk_index}
                  ref={isActive ? activeRef : undefined}
                  className={`text-xs leading-relaxed rounded px-2 py-1 ${
                    isActive
                      ? 'bg-accent text-foreground border-l-2 border-primary'
                      : 'text-muted-foreground/50'
                  }`}
                >
                  <span className={isActive ? '' : 'line-clamp-2'}>{c.content}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export function CitationMarker({ citationId, chunkIndex, conversationId }: CitationMarkerProps) {
  const citation = useCitationStore((s) => s.citations[conversationId]?.[citationId])
  const openTool = usePanelStore((s) => s.openTool)

  const handleOpenPanel = useCallback(() => {
    if (!citation) return
    const chunk = citation.chunks.find((c) => c.chunk_index === chunkIndex)
    const state = useMessageStore.getState()

    // Try streaming toolResultMap first, then fall back to history messages
    let toolName = 'web_search'
    let toolArgs: Record<string, unknown> = {}
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
          if (m.role === 'tool_result' && m.tool_call_id === citation.tool_call_id) {
            toolName = m.tool_name || 'web_search'
            // Prefer raw original_content over the 【N-M】-marked .content so the
            // preview can parse it (mirrors the live SSE path).
            content = getToolResultPreviewContent(m)
            break outer
          }
          // Search subagent inner tool results
          const subagent =
            m.role === 'tool_result' && m.tool_name === 'subagent' ? getSubagentSummary(m) : null
          if (subagent?.tool_results) {
            const inner = subagent.tool_results.find(
              (tr: { tool_call_id: string }) => tr.tool_call_id === citation.tool_call_id,
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

    // Find tool call arguments (url, etc.) from assistant messages or streaming blocks
    outer2: for (const msgs of Object.values(state.messages)) {
      for (const m of msgs) {
        if (m.role === 'assistant') {
          for (const block of m.content) {
            if (block.type === 'tool_call' && block.id === citation.tool_call_id) {
              toolName = block.name
              toolArgs = block.arguments
              break outer2
            }
          }
        }
        const subagent =
          m.role === 'tool_result' && m.tool_name === 'subagent' ? getSubagentSummary(m) : null
        if (subagent?.tool_calls) {
          const tc = subagent.tool_calls.find(
            (t: { id?: string }) => t.id === citation.tool_call_id,
          )
          if (tc) {
            toolName = tc.name
            toolArgs = tc.arguments
            break outer2
          }
        }
      }
    }
    // Also check streaming blocks for tool call args
    if (Object.keys(toolArgs).length === 0) {
      for (const agent of Object.values(state.streamAgents)) {
        for (const block of agent.blocks) {
          if (block.type === 'tool_call' && block.id === citation.tool_call_id) {
            toolName = block.name
            toolArgs = block.arguments
            break
          }
        }
      }
    }

    // Fall back to citation metadata for URL if args are still empty
    if (Object.keys(toolArgs).length === 0 && citation.metadata.url) {
      toolArgs = { url: citation.metadata.url }
    }

    if (content) {
      openTool(toolName, toolArgs, content, contentType, undefined, chunk?.content)
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
