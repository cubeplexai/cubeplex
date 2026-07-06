'use client'

import { useState, useEffect, useRef, memo } from 'react'
import { useTranslations } from 'next-intl'
import type {
  AssistantMessage as AssistantMessageType,
  ContentBlock,
  PendingConfirm,
  SessionUsage,
  SubagentSummary,
  TodoItem,
  TurnUsage,
} from '@cubebox/core'
import type { AgentStream } from '@cubebox/core'
import { useArtifactStore } from '@cubebox/core'
import { Bot, ChevronDown, ChevronRight, Brain, AlertCircle } from 'lucide-react'
import { ArtifactCard } from './ArtifactCard'
import { ImageGenerationCard } from './ImageGenerationCard'
import { SubAgentCard } from './SubAgentCard'
import { SubAgentCluster } from './SubAgentCluster'
import { TaskProgressCard } from './TaskProgressCard'
import { ToolCallGroup } from './ToolCallGroup'
import { ToolCallItem } from './ToolCallItem'
import { MessageActions } from './MessageActions'
import { CopyButton, TimeChip } from './MessageMeta'
import { TokenUsageBar } from './TokenUsageBar'
import { MemoryUpdateChip } from './MemoryUpdateChip'
import { getWriteFileSummary } from '@/lib/writeFilePreview'
import { extractWidgetCode, extractJsonStringPrefix } from '@/lib/partialJson'
import { WidgetView } from '@/components/chat/widget/WidgetView'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { useNowSeconds } from '@/hooks/useNowSeconds'
import { SkillSearchResults } from './tool-results/SkillSearchResults'

interface ReasoningBlockProps {
  thinking: string
  isStreaming: boolean
  startedAt?: number
  durationMs?: number
}

function formatDuration(ms: number): string {
  if (ms < 0) return '0s'
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s > 0 ? `${m}m${s}s` : `${m}m`
}

function ReasoningBlock({ thinking, isStreaming, startedAt, durationMs }: ReasoningBlockProps) {
  const t = useTranslations('chat')
  const [isExpanded, setIsExpanded] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const prevStreamingRef = useRef(isStreaming)

  // Auto-collapse when streaming ends
  useEffect(() => {
    if (!isStreaming && prevStreamingRef.current) {
      setIsExpanded(false)
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming])

  const tickerActive = isStreaming && startedAt != null
  const nowMs = useNowSeconds(tickerActive)
  const elapsed = tickerActive ? Math.max(0, nowMs - (startedAt ?? 0)) : 0

  // Auto-scroll to bottom during streaming
  useEffect(() => {
    if (isStreaming && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [thinking, isStreaming])

  const displayTime = durationMs ?? (isStreaming && startedAt ? elapsed : null)

  // Streaming: always show 3-line scroller; Completed: collapsed or fully expanded
  return (
    <div>
      {/* Header - clickable when not streaming */}
      <button
        type="button"
        onClick={() => {
          if (!isStreaming) setIsExpanded((prev) => !prev)
        }}
        className={`flex items-center gap-1.5 text-xs text-muted-foreground
          transition-colors group w-full text-left
          ${isStreaming ? 'cursor-default' : 'hover:text-foreground cursor-pointer'}`}
      >
        <span
          className="text-muted-foreground/60 group-hover:text-muted-foreground
          transition-colors"
        >
          {isStreaming ? (
            <Brain className="size-3 text-primary/70 animate-pulse" />
          ) : isExpanded ? (
            <ChevronDown className="size-3" />
          ) : (
            <ChevronRight className="size-3" />
          )}
        </span>
        {!isStreaming && <Brain className="size-3 text-muted-foreground/70" />}
        <span>{isStreaming ? t('thinking') : t('thinkingProcess')}</span>
        {displayTime != null && displayTime >= 1000 && (
          <span className="text-muted-foreground/50 ml-0.5">{formatDuration(displayTime)}</span>
        )}
      </button>

      {/* Streaming: 3-line scrolling viewport with gradient mask */}
      {isStreaming && (
        <div className="mt-1.5 relative">
          <div
            ref={scrollRef}
            className="overflow-hidden text-xs leading-[1.625] whitespace-pre-wrap italic
              pl-4 border-l-2 border-primary/30"
            style={{
              maxHeight: 'calc(1.625em * 3)',
              maskImage:
                'linear-gradient(to bottom, transparent 0%, rgba(0,0,0,0.45) 15%,' +
                ' rgba(0,0,0,1) 33%, rgba(0,0,0,1) 66%,' +
                ' rgba(0,0,0,0.45) 85%, transparent 100%)',
              WebkitMaskImage:
                'linear-gradient(to bottom, transparent 0%, rgba(0,0,0,0.45) 15%,' +
                ' rgba(0,0,0,1) 33%, rgba(0,0,0,1) 66%,' +
                ' rgba(0,0,0,0.45) 85%, transparent 100%)',
            }}
          >
            <span className="text-muted-foreground/70">{thinking}</span>
          </div>
        </div>
      )}

      {/* Completed & expanded: full content */}
      {!isStreaming && isExpanded && (
        <div className="mt-1.5 pl-4 border-l-2 border-border/50 max-h-64 overflow-y-auto">
          <p
            className="text-xs text-muted-foreground/70 leading-relaxed whitespace-pre-wrap
            italic"
          >
            {thinking}
          </p>
        </div>
      )}
    </div>
  )
}

interface HistoryProps {
  message: AssistantMessageType
  subagentDataMap?: Record<string, SubagentSummary>
  toolResultMap: Record<string, { content: string; receivedAt: number }>
  conversationId?: string
  workspaceId?: string | null
  isGroupChat?: boolean
  // Whether a turn is streaming somewhere in this conversation. If
  // ``activeRunId === message.run_id`` the fork action greys out for
  // this bubble (mid-turn fork is rejected by the backend with
  // run_not_completed).
  activeRunId?: string | null
  isStreamingTurn?: boolean
  // Whether to render the per-turn action row (token chip, fork button,
  // memory chip if last run) for this bubble. Set by MessageList for the
  // *last* assistant message of each completed run, so the row anchors
  // 1:1 to cubepi's run-granular ``cp.fork(after_run_id=...)`` —
  // intermediate tool-use bubbles in a multi-step turn would all collapse
  // to the same fork point.
  showForkAction?: boolean
  // Per-run token usage summed from this run's assistant messages. Drives
  // the per-turn TokenUsageBar; null hides the chip.
  turnUsage?: TurnUsage | null
  // The run's text output joined across every assistant message sharing
  // its run_id (thinking + tool_call blocks filtered out). Powers the
  // copy button so a multi-step turn copies as one block.
  turnCopyText?: string
  // True when this anchor is the latest completed run in the conversation.
  // Gates the session/context view inside TokenUsageBar and the
  // MemoryUpdateChip — both are conversation-level signals that only make
  // sense at the tail.
  isLastRun?: boolean
  sessionUsage?: SessionUsage | null
  contextWindow?: number | null
  contextTokens?: number | null
  stream?: never
  isStreaming?: never
  statusPhase?: never
  subAgentStreams?: never
  todos?: never
  pendingConfirmMap?: Record<string, PendingConfirm>
  onSandboxConfirm?: (toolCallId: string, decision: 'approve' | 'deny') => Promise<void>
}

interface StreamingProps {
  message?: never
  subagentDataMap?: never
  toolResultMap: Record<string, { content: string; receivedAt: number }>
  conversationId?: string
  workspaceId?: string | null
  isGroupChat?: boolean
  activeRunId?: string | null
  isStreamingTurn?: boolean
  showForkAction?: never
  turnUsage?: never
  turnCopyText?: never
  isLastRun?: never
  sessionUsage?: never
  contextWindow?: never
  contextTokens?: never
  stream: AgentStream
  isStreaming: boolean
  statusPhase?: string | null
  subAgentStreams?: Record<string, AgentStream>
  todos?: TodoItem[]
  pendingConfirmMap?: Record<string, PendingConfirm>
  onSandboxConfirm?: (toolCallId: string, decision: 'approve' | 'deny') => Promise<void>
}

type AssistantMessageProps = HistoryProps | StreamingProps

import { proseClasses } from '@/lib/utils'

/** Convert a consolidated SubagentSummary to an AgentStream for SubAgentCard */
function subagentSummaryToStream(summary: SubagentSummary): AgentStream {
  return {
    text: summary.text,
    toolCalls: summary.tool_calls.map((tc, i) => ({
      type: 'tool_call' as const,
      timestamp: '',
      data: {
        tool_call_id: tc.id ?? `hist-${i}`,
        name: tc.name,
        arguments: tc.arguments,
      },
      agent_id: null,
      agent_name: null,
    })),
    toolResults: [],
    thinking: summary.thinking,
    blocks: [],
    name: null,
  }
}

/** True when a tool_call invokes the platform-skills "find" operation.
 *
 * History: `find_skills` → `skills(operation='find', ...)` → `platform_skills_find`.
 * The capability was migrated to per-operation deferred tools in the cubepi
 * dispatch upgrade, so the tool name now carries the operation directly and
 * the umbrella `operation` argument is gone. We still match the legacy
 * shape so streamed messages persisted from older runs keep rendering the
 * candidate-card UI. */
function isSkillsFindCall(block: ContentBlock): boolean {
  if (block.type !== 'tool_call') return false
  if (block.name === 'platform_skills_find') return true
  // Legacy umbrella shape — kept for historical messages.
  if (block.name === 'skills') {
    const args = (block.arguments ?? {}) as { operation?: unknown }
    return args.operation === 'find'
  }
  return false
}

function ContentBlockRenderer({
  block,
  index,
  isLast,
  isStreaming,
  subAgentStreams,
  subagentDataMap,
  toolResultMap,
  messageCreatedAt,
  subagentIndex,
  agentId,
  conversationId,
  pendingConfirmMap,
  onSandboxConfirm,
}: {
  block: ContentBlock
  index: number
  isLast: boolean
  isStreaming: boolean
  subAgentStreams?: Record<string, AgentStream>
  subagentDataMap?: Record<string, SubagentSummary>
  toolResultMap: Record<string, { content: string; receivedAt: number }>
  messageCreatedAt?: string
  subagentIndex?: number
  agentId?: string | null
  conversationId?: string
  pendingConfirmMap?: Record<string, PendingConfirm>
  onSandboxConfirm?: (toolCallId: string, decision: 'approve' | 'deny') => Promise<void>
}) {
  if (block.type === 'thinking') {
    return (
      <div className="bg-card border border-border rounded-xl px-3 py-2.5">
        <ReasoningBlock
          thinking={block.thinking}
          isStreaming={isStreaming && isLast}
          startedAt={block.started_at}
          durationMs={block.duration_ms}
        />
      </div>
    )
  }
  if (block.type === 'tool_call' && block.name === 'subagent') {
    const agentKey = `subagent:${block.id}`
    const stream = subAgentStreams?.[agentKey]
    const historicalStream =
      !stream && subagentDataMap?.[agentKey]
        ? subagentSummaryToStream(subagentDataMap[agentKey])
        : undefined
    const args = block.arguments as {
      name?: string
      role?: string
      task?: string
    }
    return (
      <SubAgentCard
        name={args.name ?? 'Subagent'}
        role={args.role ?? ''}
        task={args.task ?? ''}
        index={subagentIndex ?? 1}
        agentId={agentKey}
        stream={stream ?? historicalStream}
        isRunning={isStreaming && !!stream && !toolResultMap[block.id]}
        toolResultMap={toolResultMap}
        conversationId={conversationId}
      />
    )
  }
  if (block.type === 'tool_call' && block.name === 'save_artifact') {
    const args = block.arguments as { name?: string; artifact_id?: string }
    // Look up artifact from store by parsing the tool result
    const toolResult = toolResultMap[block.id]
    let artifact = null
    if (toolResult?.content) {
      try {
        const parsed = JSON.parse(toolResult.content)
        if (parsed.artifact) artifact = parsed.artifact
      } catch {
        /* ignore */
      }
    }
    // Fallback: try to find by id or name in the artifact store
    if (!artifact && (args.artifact_id || args.name)) {
      const allArtifacts = useArtifactStore.getState().artifacts
      for (const convArtifacts of Object.values(allArtifacts)) {
        if (args.artifact_id && convArtifacts[args.artifact_id]) {
          artifact = convArtifacts[args.artifact_id]
          break
        }
        if (args.name) {
          const match = Object.values(convArtifacts).find((a) => a.name === args.name)
          if (match) {
            artifact = match
            break
          }
        }
      }
    }
    if (artifact) {
      return <ArtifactCard artifact={artifact} />
    }
    if (isStreaming || !toolResult) {
      return null
    }
    // Fallback to regular tool call rendering
    return (
      <ToolCallGroup
        blocks={[block as ContentBlock & { type: 'tool_call' }]}
        toolResultMap={toolResultMap}
        isStreaming={isStreaming}
        messageCreatedAt={messageCreatedAt}
        agentId={agentId}
        pendingConfirmMap={pendingConfirmMap}
        onSandboxConfirm={onSandboxConfirm}
      />
    )
  }
  if (block.type === 'tool_call' && block.name === 'generate_image') {
    const args = block.arguments as { prompt?: string }
    const toolResult = toolResultMap[block.id]
    let artifact = null
    if (toolResult?.content) {
      try {
        const parsed = JSON.parse(toolResult.content)
        if (parsed.artifact) artifact = parsed.artifact
      } catch {
        /* ignore */
      }
    }
    return <ImageGenerationCard prompt={args.prompt ?? ''} artifact={artifact} />
  }
  if (block.type === 'tool_call' && isSkillsFindCall(block)) {
    const toolResult = toolResultMap[block.id]
    if (!toolResult) return null
    let hasCandidates = false
    try {
      const parsed = JSON.parse(toolResult.content) as { candidates?: unknown }
      hasCandidates = Array.isArray(parsed.candidates)
    } catch {
      // invalid JSON — fall through to generic tool rendering below
    }
    if (hasCandidates) {
      return <SkillSearchResults key={block.id} resultJson={toolResult.content} />
    }
  }
  if (block.type === 'tool_call' && block.name === 'show_widget') {
    const a = block.arguments ?? {}
    return (
      <WidgetView
        key={block.id}
        widgetId={block.id}
        widgetCode={typeof a.widget_code === 'string' ? a.widget_code : ''}
        status="complete"
        title={typeof a.title === 'string' ? a.title : undefined}
        width={typeof a.width === 'number' ? a.width : undefined}
        height={typeof a.height === 'number' ? a.height : undefined}
      />
    )
  }
  if (block.type === 'tool_call') {
    return (
      <ToolCallGroup
        blocks={[block as ContentBlock & { type: 'tool_call' }]}
        toolResultMap={toolResultMap}
        isStreaming={isStreaming}
        messageCreatedAt={messageCreatedAt}
        agentId={agentId}
        pendingConfirmMap={pendingConfirmMap}
        onSandboxConfirm={onSandboxConfirm}
      />
    )
  }
  if (block.type === 'tool_call_streaming' && block.name === 'show_widget') {
    const widgetId = block.tool_call_id ?? `idx-${block.index}`
    const code = extractWidgetCode(block.args_text)
    const title = extractJsonStringPrefix(block.args_text, 'title') || undefined
    if (!code) {
      return (
        <div className="rounded-lg border border-border bg-muted p-3 text-sm text-muted-foreground">
          {`Preparing widget${title ? ` (${title})` : ''}…`}
        </div>
      )
    }
    return (
      <WidgetView
        key={widgetId}
        widgetId={widgetId}
        widgetCode={code}
        status="streaming"
        title={title}
      />
    )
  }
  if (block.type === 'tool_call_streaming' && block.name === 'ask_user') {
    // ask_user streams its args before the ask_user_request event fires.
    // Showing a generic tool-call card here would briefly render a raw-args
    // placeholder, disappear once the call finalises (ToolCallGroup
    // suppresses ask_user without a result), then reappear as the
    // <AskUserCard>. Suppress all three transitions; the live AskUserCard
    // is the canonical render.
    return null
  }
  if (block.type === 'tool_call_streaming' && block.name === 'generate_image') {
    const prompt = extractJsonStringPrefix(block.args_text, 'prompt')
    return <ImageGenerationCard prompt={prompt} artifact={null} />
  }
  if (block.type === 'tool_call_streaming') {
    const supportsPreview = block.name === 'write_file'
    return (
      <div
        className="bg-card border border-border rounded-xl
          overflow-hidden border-l-2
          border-l-muted-foreground/20"
      >
        <ToolCallItem
          name={block.name || 'tool'}
          arguments={{}}
          toolCallId={block.tool_call_id ?? `streaming-${index}`}
          summaryOverride={
            supportsPreview
              ? getWriteFileSummary({}, block.args_text)
              : block.args_text.trim() || undefined
          }
          contentTypeOverride={supportsPreview ? 'write_file' : undefined}
          toolRef={
            supportsPreview
              ? {
                  agent_id: agentId ?? null,
                  tool_call_id: block.tool_call_id,
                  index: block.index,
                }
              : undefined
          }
          timestamp={messageCreatedAt}
          isPending={true}
          allowOpenWhenPending={supportsPreview}
        />
      </div>
    )
  }
  if (block.type === 'text') {
    return (
      <MarkdownWithCitations className={proseClasses} conversationId={conversationId}>
        {block.text}
      </MarkdownWithCitations>
    )
  }

  const _exhaustive: never = block
  return _exhaustive
}

/** Group consecutive tool_call blocks for compact rendering (subagent calls render individually) */
function groupBlocks(blocks: ContentBlock[]): (ContentBlock | ContentBlock[])[] {
  const result: (ContentBlock | ContentBlock[])[] = []
  for (const block of blocks) {
    if (
      block.type === 'tool_call' &&
      block.name !== 'subagent' &&
      block.name !== 'save_artifact' &&
      block.name !== 'generate_image' &&
      block.name !== 'write_todos' &&
      block.name !== 'show_widget' &&
      !isSkillsFindCall(block)
    ) {
      const last = result[result.length - 1]
      if (
        Array.isArray(last) &&
        last[0].type === 'tool_call' &&
        (last[0] as ContentBlock & { name: string }).name !== 'subagent'
      ) {
        last.push(block)
      } else {
        result.push([block])
      }
    } else if (block.type === 'tool_call' && block.name === 'write_todos') {
      continue
    } else {
      result.push(block)
    }
  }
  return result
}

function extractTodosFromMessage(msg: AssistantMessageType): TodoItem[] {
  const toolCalls = msg.content.filter(
    (b): b is Extract<ContentBlock, { type: 'tool_call' }> => b.type === 'tool_call',
  )
  const tc = toolCalls.findLast((c) => c.name === 'write_todos')
  if (!tc) return []
  const raw = Array.isArray(tc.arguments.todos) ? tc.arguments.todos : []
  const result: TodoItem[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const t = item as { content?: unknown; status?: unknown }
    const desc = typeof t.content === 'string' ? t.content.trim() : ''
    if (!desc) continue
    const status = t.status === 'in_progress' || t.status === 'completed' ? t.status : 'pending'
    result.push({ id: null, description: desc, status })
  }
  return result
}

export function AssistantMessage({
  message,
  stream,
  isStreaming,
  statusPhase,
  subAgentStreams,
  subagentDataMap,
  toolResultMap,
  todos,
  conversationId,
  workspaceId,
  isGroupChat,
  activeRunId,
  isStreamingTurn,
  showForkAction,
  turnUsage,
  turnCopyText,
  isLastRun,
  sessionUsage,
  contextWindow,
  contextTokens,
  pendingConfirmMap,
  onSandboxConfirm,
}: AssistantMessageProps) {
  const t = useTranslations('chat')
  const streamAgentId = stream ? 'main' : undefined
  const blocks: ContentBlock[] = stream ? stream.blocks : message!.content

  const historyTodos = message ? extractTodosFromMessage(message) : []

  const msgCreatedAt = message?.timestamp
    ? new Date(message.timestamp * 1000).toISOString()
    : undefined

  const _hasContent = blocks.length > 0
  const grouped = groupBlocks(blocks)

  // History assistants persisted from a failed turn have stop_reason="error"
  // and an empty content list — without this branch the bubble renders nothing
  // and the user sees a silent gap after their question. Surface the upstream
  // error_message so the failure is at least visible (and addressable).
  const errorMessage = (message?.error_message ?? '').trim()
  const showErrorBubble = !isStreaming && message?.stop_reason === 'error' && errorMessage !== ''

  // Count subagent blocks for index assignment and cluster display
  let subagentCounter = 0
  const subagentIndexMap = new Map<number, number>()
  for (let i = 0; i < grouped.length; i++) {
    const item = grouped[i]
    if (!Array.isArray(item) && item.type === 'tool_call' && item.name === 'subagent') {
      subagentCounter++
      subagentIndexMap.set(i, subagentCounter)
    }
  }
  const totalSubagents = subagentCounter

  // Count active subagents (streaming)
  const activeSubagentCount = subAgentStreams ? Object.keys(subAgentStreams).length : 0

  const renderItem = (item: (typeof grouped)[number], i: number) => {
    if (Array.isArray(item)) {
      const tcBlocks = item as (ContentBlock & { type: 'tool_call' })[]
      return (
        <ToolCallGroup
          key={i}
          blocks={tcBlocks}
          toolResultMap={toolResultMap}
          isStreaming={isStreaming === true}
          messageCreatedAt={msgCreatedAt}
          agentId={streamAgentId}
          pendingConfirmMap={pendingConfirmMap}
          onSandboxConfirm={onSandboxConfirm}
        />
      )
    }
    return (
      <ContentBlockRenderer
        key={i}
        block={item}
        index={i}
        isLast={i === grouped.length - 1}
        isStreaming={isStreaming === true}
        subAgentStreams={subAgentStreams}
        subagentDataMap={subagentDataMap}
        toolResultMap={toolResultMap}
        messageCreatedAt={msgCreatedAt}
        subagentIndex={subagentIndexMap.get(i)}
        agentId={streamAgentId}
        conversationId={conversationId}
        pendingConfirmMap={pendingConfirmMap}
        onSandboxConfirm={onSandboxConfirm}
      />
    )
  }

  return (
    <div data-role="assistant" className="group space-y-2">
      {(grouped.length > 0 || totalSubagents >= 2 || showErrorBubble) && (
        <div className="flex justify-start gap-2.5">
          <div
            className="shrink-0 w-6 h-6 rounded-md border border-border bg-card
        flex items-center justify-center mt-0.5"
          >
            <Bot className="size-3.5 text-primary/70" />
          </div>
          <div className="flex-1 max-w-[75%] space-y-2">
            {totalSubagents >= 2 && (
              <SubAgentCluster
                activeCount={isStreaming === true ? activeSubagentCount : 0}
                totalCount={totalSubagents}
              />
            )}
            {grouped.map((item, i) => renderItem(item, i))}
            {showErrorBubble && (
              <div
                role="alert"
                className="flex items-start gap-2 px-3 py-2.5 rounded-lg
                  bg-destructive/10 border border-destructive/20 text-destructive text-sm"
              >
                <AlertCircle className="size-4 shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="font-medium">{t('assistantReplyFailed')}</div>
                  <pre
                    className="whitespace-pre-wrap break-words font-mono text-xs leading-snug
                      opacity-90 max-h-48 overflow-auto"
                  >
                    {errorMessage}
                  </pre>
                </div>
              </div>
            )}
            {message && conversationId && showForkAction && (
              <div
                className="flex flex-wrap items-center gap-1 opacity-0
                  transition-opacity group-hover:opacity-100 focus-within:opacity-100"
              >
                <CopyButton content={turnCopyText ?? ''} />
                {turnUsage && (
                  <TokenUsageBar
                    turnUsage={turnUsage}
                    sessionUsage={isLastRun ? (sessionUsage ?? null) : null}
                    contextWindow={isLastRun ? (contextWindow ?? null) : null}
                    contextTokens={isLastRun ? (contextTokens ?? null) : null}
                    showSessionView={isLastRun ?? false}
                  />
                )}
                {isLastRun && workspaceId && (
                  <MemoryUpdateChip conversationId={conversationId} workspaceId={workspaceId} />
                )}
                <MessageActions
                  conversationId={conversationId}
                  workspaceId={workspaceId ?? null}
                  runId={message.run_id}
                  isGroupChat={isGroupChat ?? false}
                  activeRunId={activeRunId ?? null}
                  isStreaming={isStreamingTurn ?? false}
                />
                <TimeChip timestamp={message.timestamp ?? null} />
              </div>
            )}
          </div>
        </div>
      )}
      {(isStreaming && todos && todos.length > 0) ||
      (!isStreaming && historyTodos.length > 0) ||
      isStreaming ? (
        <div className="flex justify-start gap-2.5">
          <div className="shrink-0 w-6" aria-hidden />
          <div className="flex-1 max-w-[75%] space-y-2">
            {isStreaming && todos && todos.length > 0 && (
              <TaskProgressCard todos={todos} isStreaming={true} />
            )}
            {!isStreaming && historyTodos.length > 0 && (
              <TaskProgressCard todos={historyTodos} isStreaming={false} />
            )}
            {isStreaming && (
              <div data-testid="loading-indicator" className="flex items-center gap-1 pl-1 h-6">
                {statusPhase === 'preparing' || statusPhase === 'loading_tools' ? (
                  <span className="text-xs text-muted-foreground animate-pulse">
                    {statusPhase === 'preparing' ? t('runPreparing') : t('runLoadingTools')}
                  </span>
                ) : statusPhase === 'starting' ? (
                  <span className="text-xs text-muted-foreground animate-pulse">
                    {t('runStarting')}
                  </span>
                ) : statusPhase === 'sandbox_creating' ? (
                  <span className="text-xs text-muted-foreground animate-pulse">
                    {t('sandboxPreparing')}
                  </span>
                ) : statusPhase === 'sandbox_failed' ? (
                  <span className="text-xs text-destructive">{t('sandboxFailed')}</span>
                ) : (
                  <>
                    <span
                      className="w-1.5 h-1.5 rounded-full bg-primary
                  animate-bounce [animation-delay:0ms]"
                    />
                    <span
                      className="w-1.5 h-1.5 rounded-full bg-primary
                  animate-bounce [animation-delay:150ms]"
                    />
                    <span
                      className="w-1.5 h-1.5 rounded-full bg-primary
                  animate-bounce [animation-delay:300ms]"
                    />
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}

/**
 * Memoized wrapper for historical (non-streaming) assistant messages. The
 * perf win comes from stabilizing `toolResultMap`: MessageList passes the
 * message-stable `historicalToolResults` (not the live `mergedToolResultMap`,
 * which mutates on every streaming `tool_result` event).
 *
 * `pendingConfirmMap` and `onSandboxConfirm` are still passed in — a sandbox
 * confirm can outlive a `__commitTurnAndInject` (steer / injected_message),
 * so historical bubbles must keep rendering the approve/deny card. Those
 * props are reference-stable across text/reasoning deltas (the map only
 * mutates on `sandbox_confirm_request` / `_resolved`, and the handler is
 * useCallback'd with deps that exclude per-delta state), so memo still bails
 * out on the hot path.
 */
export const HistoryAssistantMessage = memo(AssistantMessage)
