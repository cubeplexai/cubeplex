'use client'

import { use, useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  getPublicShare,
  type PublicShare,
  type PublicShareArtifact,
  type Message,
  type ContentBlock,
} from '@cubebox/core'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { WidgetView } from '@/components/chat/widget/WidgetView'
import { ToolCallGroup } from '@/components/chat/ToolCallGroup'
import { proseClasses } from '@/lib/utils'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type ToolCallBlock = Extract<ContentBlock, { type: 'tool_call' }>
type ToolResultMap = Record<string, { content: string; receivedAt: number }>

const HIDDEN_TOOLS = new Set(['show_widget', 'write_todos'])

function buildToolResultMap(messages: Message[]): ToolResultMap {
  const map: ToolResultMap = {}
  for (const m of messages) {
    if (m.role !== 'tool_result') continue
    const trMsg = m as Extract<Message, { role: 'tool_result' }>
    const text = m.content
      .filter((b): b is Extract<typeof b, { type: 'text' }> => b.type === 'text')
      .map((b) => (b as { type: 'text'; text: string }).text)
      .join('')
    map[trMsg.tool_call_id] = {
      content: text,
      receivedAt: trMsg.timestamp ? trMsg.timestamp * 1000 : Date.now(),
    }
  }
  return map
}

/** Group consecutive non-special tool_call blocks for compact rendering. */
function groupBlocks(
  blocks: ContentBlock[],
): Array<ContentBlock | { _group: true; blocks: ToolCallBlock[] }> {
  const result: Array<ContentBlock | { _group: true; blocks: ToolCallBlock[] }> = []
  for (const block of blocks) {
    if (
      block.type === 'tool_call' &&
      !HIDDEN_TOOLS.has(block.name) &&
      block.name !== 'save_artifact'
    ) {
      const last = result[result.length - 1]
      if (last && '_group' in last) {
        last.blocks.push(block as ToolCallBlock)
      } else {
        result.push({ _group: true, blocks: [block as ToolCallBlock] })
      }
    } else {
      result.push(block)
    }
  }
  return result
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function AssistantBlocks({
  blocks,
  toolResultMap,
  messageCreatedAt,
}: {
  blocks: ContentBlock[]
  toolResultMap: ToolResultMap
  messageCreatedAt?: string
}) {
  const grouped = groupBlocks(blocks)
  return (
    <div className="space-y-2 mb-3">
      {grouped.map((item, i) => {
        if ('_group' in item) {
          return (
            <ToolCallGroup
              key={item.blocks[0].id ?? i}
              blocks={item.blocks}
              toolResultMap={toolResultMap}
              isStreaming={false}
              messageCreatedAt={messageCreatedAt}
            />
          )
        }
        const block = item
        if (block.type === 'text' && block.text.trim()) {
          return (
            <div
              key={i}
              className="max-w-[80%] rounded-2xl px-4 py-2.5 bg-muted text-foreground
                text-sm leading-relaxed"
            >
              <MarkdownWithCitations className={proseClasses} conversationId="">
                {block.text}
              </MarkdownWithCitations>
            </div>
          )
        }
        if (block.type === 'tool_call' && block.name === 'show_widget') {
          const a = (block as ToolCallBlock).arguments ?? {}
          return (
            <WidgetView
              key={(block as ToolCallBlock).id}
              widgetId={(block as ToolCallBlock).id}
              widgetCode={typeof a.widget_code === 'string' ? a.widget_code : ''}
              status="complete"
              title={typeof a.title === 'string' ? a.title : undefined}
              width={typeof a.width === 'number' ? a.width : undefined}
              height={typeof a.height === 'number' ? a.height : undefined}
            />
          )
        }
        return null
      })}
    </div>
  )
}

function SharedMessage({
  message,
  toolResultMap,
}: {
  message: Message
  toolResultMap: ToolResultMap
}) {
  const role = message.role

  const textContent = message.content
    .filter((b): b is Extract<typeof b, { type: 'text' }> => b.type === 'text')
    .map((b) => (b as { type: 'text'; text: string }).text)
    .join('')

  if (role === 'user') {
    return (
      <div className="flex justify-end mb-3">
        <div
          className="max-w-[75%] rounded-2xl px-4 py-2.5 bg-primary/10 text-foreground
          text-sm leading-relaxed whitespace-pre-wrap"
        >
          {textContent}
        </div>
      </div>
    )
  }

  const createdAt = message.timestamp ? new Date(message.timestamp * 1000).toISOString() : undefined

  return (
    <AssistantBlocks
      blocks={message.content}
      toolResultMap={toolResultMap}
      messageCreatedAt={createdAt}
    />
  )
}

function SharedArtifact({ artifact, shareId }: { artifact: PublicShareArtifact; shareId: string }) {
  const [previewing, setPreviewing] = useState(false)
  const filename = artifact.entry_file ?? artifact.path.split('/').pop()
  const href = filename
    ? `/api/v1/shares/${shareId}/artifacts/${artifact.id}/v${artifact.version}/${filename}`
    : null
  const isHtml = artifact.mime_type?.startsWith('text/html') || filename?.endsWith('.html')

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3 group">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground truncate">{artifact.name}</p>
          {artifact.description && (
            <p className="text-xs text-muted-foreground mt-0.5 truncate">{artifact.description}</p>
          )}
          <p className="text-xs text-muted-foreground/60 mt-0.5">
            {artifact.mime_type ?? artifact.artifact_type} · v{artifact.version}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {isHtml && (
            <button
              onClick={() => setPreviewing((p) => !p)}
              className="rounded-md p-1.5 text-muted-foreground hover:text-primary
                hover:bg-muted transition-colors"
              title={previewing ? 'Hide preview' : 'Preview'}
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </button>
          )}
          {href && (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md p-1.5 text-muted-foreground hover:text-primary
                hover:bg-muted transition-colors"
              title="Open in new tab"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                <polyline points="15 3 21 3 21 9" />
                <line x1="10" y1="14" x2="21" y2="3" />
              </svg>
            </a>
          )}
        </div>
      </div>
      {previewing && href && (
        <div className="border-t border-border">
          <iframe
            src={href}
            className="w-full border-0"
            style={{ height: 480 }}
            sandbox="allow-scripts"
            title={artifact.name}
          />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SharePage({ params }: { params: Promise<{ shrId: string }> }) {
  const { shrId } = use(params)
  const t = useTranslations('publicShare')
  const tTime = useTranslations('time')

  const [share, setShare] = useState<PublicShare | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getPublicShare(shrId)
      .then((data) => {
        if (!cancelled) {
          setShare(data)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Unknown error')
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [shrId])

  function formatDate(iso: string): string {
    const d = new Date(iso)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffMins = Math.floor(diffMs / 60_000)
    const diffHours = Math.floor(diffMs / 3_600_000)
    const diffDays = Math.floor(diffMs / 86_400_000)
    if (diffMins < 1) return tTime('justNow')
    if (diffMins < 60) return tTime('minutesAgo', { n: diffMins })
    if (diffHours < 24) return tTime('hoursAgo', { n: diffHours })
    if (diffDays < 30) return tTime('daysAgo', { n: diffDays })
    return d.toLocaleDateString()
  }

  // Loading state
  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center min-h-screen">
        <div className="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin" />
      </div>
    )
  }

  // Error / not found state
  if (error || !share) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center min-h-screen gap-3 px-4 text-center">
        <h1 className="text-xl font-semibold text-foreground">{t('notFound')}</h1>
        <p className="text-sm text-muted-foreground max-w-sm">{t('notFoundBody')}</p>
        <p className="text-xs text-muted-foreground/50 mt-4">{t('poweredBy')}</p>
      </div>
    )
  }

  const messages = share.messages as Message[]
  const toolResultMap = buildToolResultMap(messages)
  const visibleMessages = messages.filter((m) => {
    if (m.metadata?.synthetic === true) return false
    if (m.role === 'tool_result') return false
    return true
  })

  return (
    <div className="flex flex-col min-h-screen">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b border-border bg-background/80 backdrop-blur-sm">
        <div className="max-w-3xl mx-auto px-4 h-14 flex items-center justify-between gap-4">
          {/* Logo */}
          <span className="font-semibold text-foreground tracking-tight select-none">cubebox</span>

          {/* Title */}
          <h1 className="flex-1 text-center text-sm font-medium text-foreground truncate px-2">
            {share.title}
          </h1>

          {/* Shared by */}
          <div className="text-right shrink-0">
            <p className="text-xs text-muted-foreground">
              {t('sharedBy', { name: share.creator_display_name })}
            </p>
            <p className="text-xs text-muted-foreground/60">{formatDate(share.created_at)}</p>
          </div>
        </div>
      </header>

      {/* Messages */}
      <main className="flex-1 max-w-3xl w-full mx-auto px-4 py-6">
        <div className="space-y-1">
          {visibleMessages.map((msg, idx) => (
            <SharedMessage key={msg.id ?? idx} message={msg} toolResultMap={toolResultMap} />
          ))}
        </div>

        {/* Artifacts */}
        {share.artifacts.length > 0 && (
          <section className="mt-8 pt-6 border-t border-border">
            <h2 className="text-sm font-semibold text-foreground mb-3">
              Artifacts ({share.artifacts.length})
            </h2>
            <div className="space-y-2">
              {share.artifacts.map((artifact) => (
                <SharedArtifact key={artifact.id} artifact={artifact} shareId={share.id} />
              ))}
            </div>
          </section>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-border py-4 text-center">
        <p className="text-xs text-muted-foreground/50">{t('poweredBy')}</p>
      </footer>
    </div>
  )
}
