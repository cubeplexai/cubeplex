'use client'

import { useState, useEffect, useRef, memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react'
import type { AgentStream } from '@cubebox/core'
import { ToolCallItem } from './ToolCallItem'
import { AgentAvatar } from './AgentAvatar'
import { proseClasses } from '@/lib/utils'

interface Props {
  name: string
  role: string
  task: string
  index: number
  stream?: AgentStream
  isRunning: boolean
  toolResultMap: Record<string, { content: string; receivedAt: number }>
}

function formatDuration(ms: number): string {
  if (ms < 0) return '0s'
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s > 0 ? `${m}m${s}s` : `${m}m`
}

export const SubAgentCard = memo(function SubAgentCard({
  name,
  role,
  task,
  index,
  stream,
  isRunning,
  toolResultMap,
}: Props) {
  const [expanded, setExpanded] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const startedAt = useRef(Date.now())
  const scrollRef = useRef<HTMLDivElement>(null)

  // Reset start time when component mounts (new agent run)
  useEffect(() => {
    startedAt.current = Date.now()
  }, [])

  // Live elapsed timer
  useEffect(() => {
    if (!isRunning) return
    const tick = () => setElapsed(Date.now() - startedAt.current)
    tick()
    const interval = setInterval(tick, 1000)
    return () => clearInterval(interval)
  }, [isRunning])

  // Auto-scroll streaming content
  useEffect(() => {
    if (isRunning && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [stream?.toolCalls.length, stream?.text, isRunning])

  const toolCalls = stream?.toolCalls ?? []
  const completedCount = toolCalls.filter(
    (tc) => toolResultMap[tc.data.tool_call_id],
  ).length
  const pendingTc = toolCalls.find(
    (tc) => !toolResultMap[tc.data.tool_call_id],
  )
  const hasContent = stream && (toolCalls.length > 0 || stream.text)
  const displayTime = isRunning ? elapsed : (hasContent ? elapsed : 0)

  return (
    <div className="border border-border rounded-xl overflow-hidden bg-muted/10 border-l-2
      border-l-primary/40">
      {/* Header */}
      <div className="flex items-start gap-2.5 px-3 py-2.5">
        <AgentAvatar seed={name} size={32} className="rounded-md shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm text-foreground">{name}</span>
            <span className="text-xs text-muted-foreground bg-muted/50 px-1.5 py-0.5
              rounded-md">{role}</span>
            <span className="ml-auto text-xs text-muted-foreground/50 font-mono tabular-nums">
              {String(index).padStart(2, '0')}
            </span>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5 truncate">{task}</p>
        </div>
      </div>

      {/* Content area — collapsed by default, expandable */}
      {hasContent && (
        <div className="border-t border-border">
          {/* Streaming viewport (always visible when running, shows last few items) */}
          {!expanded && (
            <div
              ref={scrollRef}
              className="overflow-hidden"
              style={{
                maxHeight: 'calc(2.5rem * 3)',
                maskImage: isRunning
                  ? 'linear-gradient(to bottom, transparent 0%, black 20%, black 80%, transparent 100%)'
                  : 'linear-gradient(to bottom, black 0%, black 80%, transparent 100%)',
                WebkitMaskImage: isRunning
                  ? 'linear-gradient(to bottom, transparent 0%, black 20%, black 80%, transparent 100%)'
                  : 'linear-gradient(to bottom, black 0%, black 80%, transparent 100%)',
              }}
            >
              {toolCalls.map((tc, i) => {
                const result = toolResultMap[tc.data.tool_call_id] ?? null
                return (
                  <ToolCallItem
                    key={tc.data.tool_call_id || i}
                    name={tc.data.name}
                    arguments={tc.data.arguments}
                    toolCallId={tc.data.tool_call_id}
                    toolResult={result}
                    timestamp={tc.timestamp}
                    isPending={isRunning && !result}
                  />
                )
              })}
            </div>
          )}

          {/* Expanded full content */}
          {expanded && (
            <div className="max-h-80 overflow-y-auto">
              {toolCalls.map((tc, i) => {
                const result = toolResultMap[tc.data.tool_call_id] ?? null
                return (
                  <ToolCallItem
                    key={tc.data.tool_call_id || i}
                    name={tc.data.name}
                    arguments={tc.data.arguments}
                    toolCallId={tc.data.tool_call_id}
                    toolResult={result}
                    timestamp={tc.timestamp}
                    isPending={isRunning && !result}
                    showDivider={i > 0}
                  />
                )
              })}
              {stream?.text && (
                <div className={`px-3 py-2 border-t border-border ${proseClasses}`}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{stream.text}</ReactMarkdown>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Footer: activity dots + expand toggle + elapsed time */}
      <div className="flex items-center gap-1.5 px-3 py-1.5 border-t border-border">
        {/* Activity dots */}
        <div className="flex items-center gap-1">
          {Array.from({ length: completedCount }, (_, i) => (
            <span key={`done-${i}`} className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          ))}
          {isRunning && pendingTc && (
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
          )}
          {isRunning && !pendingTc && (
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
          )}
        </div>

        {/* Expand/collapse toggle */}
        {hasContent && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="ml-1 flex items-center gap-0.5 text-xs text-muted-foreground
              hover:text-foreground transition-colors"
          >
            {expanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
            <span>{expanded ? '收起' : '展开'}</span>
          </button>
        )}

        {/* Running indicator + elapsed time */}
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          {isRunning && (
            <span className="flex gap-0.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="w-1 h-1 rounded-full bg-muted-foreground animate-pulse"
                  style={{ animationDelay: `${i * 200}ms` }}
                />
              ))}
            </span>
          )}
          {!isRunning && hasContent && <CheckCircle2 className="size-3 text-emerald-500" />}
          {displayTime >= 1000 && <span>{formatDuration(displayTime)}</span>}
        </span>
      </div>

      {/* Empty running state */}
      {!hasContent && isRunning && (
        <div className="px-3 pb-2.5">
          <span className="text-xs text-muted-foreground animate-pulse">正在执行...</span>
        </div>
      )}
    </div>
  )
})
