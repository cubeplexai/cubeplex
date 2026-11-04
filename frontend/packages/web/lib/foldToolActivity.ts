import type { AssistantMessage, ContentBlock, Message } from '@cubeplex/core'

type ThinkingBlock = Extract<ContentBlock, { type: 'thinking' }>
type ToolCallBlock = Extract<ContentBlock, { type: 'tool_call' }>
type StreamingToolBlock = Extract<ContentBlock, { type: 'tool_call_streaming' }>

export type ActivityBlock = ThinkingBlock | ToolCallBlock | StreamingToolBlock

export type FoldPiece =
  { kind: 'activity'; blocks: ActivityBlock[] } | { kind: 'block'; block: ContentBlock }

const OWN_CARD_TOOLS = new Set([
  'subagent',
  'save_artifact',
  'generate_image',
  'present_file',
  'show_widget',
  'platform_skills_find',
])

function isLegacySkillsFind(block: ToolCallBlock): boolean {
  if (block.name !== 'skills') return false
  const args = (block.arguments ?? {}) as { operation?: unknown }
  return args.operation === 'find'
}

function isOwnCardTool(block: ToolCallBlock | StreamingToolBlock): boolean {
  if (!block.name) return false
  if (OWN_CARD_TOOLS.has(block.name)) return true
  return block.type === 'tool_call' && isLegacySkillsFind(block)
}

function isGroupableTool(block: ContentBlock): block is ToolCallBlock | StreamingToolBlock {
  if (block.type !== 'tool_call' && block.type !== 'tool_call_streaming') return false
  if (block.name === 'write_todos') return false
  return !isOwnCardTool(block)
}

function hasTool(blocks: ActivityBlock[]): boolean {
  return blocks.some((block) => block.type === 'tool_call' || block.type === 'tool_call_streaming')
}

/**
 * Fold a reply's blocks into activity groups.
 *
 * Thinking stays with the tool calls around it. A non-blank text block, or a
 * tool that has its own card, ends the group. Blank text and write_todos do
 * not. Thinking that never meets a tool call stays a normal thinking block.
 */
export function foldToolActivity(blocks: ContentBlock[]): FoldPiece[] {
  const pieces: FoldPiece[] = []
  let pendingThinking: ThinkingBlock[] = []
  let activity: ActivityBlock[] | null = null

  const flushThinking = () => {
    for (const block of pendingThinking) pieces.push({ kind: 'block', block })
    pendingThinking = []
  }

  const flushActivity = () => {
    if (!activity) return
    if (hasTool(activity)) {
      pieces.push({ kind: 'activity', blocks: activity })
    } else {
      for (const block of activity) {
        if (block.type === 'thinking') pieces.push({ kind: 'block', block })
      }
    }
    activity = null
  }

  for (const block of blocks) {
    if (block.type === 'thinking') {
      if (activity) activity.push(block)
      else pendingThinking.push(block)
      continue
    }

    if (block.type === 'text') {
      if (!block.text.trim()) continue
      flushActivity()
      flushThinking()
      pieces.push({ kind: 'block', block })
      continue
    }

    if (
      (block.type === 'tool_call' || block.type === 'tool_call_streaming') &&
      block.name === 'write_todos'
    ) {
      continue
    }

    if (isGroupableTool(block)) {
      if (!activity) {
        activity = [...pendingThinking]
        pendingThinking = []
      }
      activity.push(block)
      continue
    }

    flushActivity()
    flushThinking()
    pieces.push({ kind: 'block', block })
  }

  flushActivity()
  flushThinking()
  return pieces
}

export type HistoryDisplayRow =
  | { kind: 'message'; message: Message }
  | { kind: 'assistant-run'; messages: AssistantMessage[]; merged: AssistantMessage }

/**
 * Join assistant rows from one run so a refresh can fold them the same way
 * the live stream does. Tool-result rows are not bubbles and do not split
 * the run. A user message, or a new run id, does.
 */
export function groupAssistantRuns(messages: Message[]): HistoryDisplayRow[] {
  const rows: HistoryDisplayRow[] = []
  let current: AssistantMessage[] = []
  let currentRunId: string | null = null

  const flush = () => {
    if (current.length === 0) return
    rows.push({
      kind: 'assistant-run',
      messages: current,
      merged: mergeAssistantRun(current),
    })
    current = []
    currentRunId = null
  }

  for (const message of messages) {
    if (message.role === 'tool_result') continue
    if (message.role === 'assistant' && message.run_id) {
      if (currentRunId === message.run_id) {
        current.push(message)
        continue
      }
      flush()
      current = [message]
      currentRunId = message.run_id
      continue
    }
    flush()
    if (message.role === 'assistant') {
      rows.push({
        kind: 'assistant-run',
        messages: [message],
        merged: message,
      })
      continue
    }
    rows.push({ kind: 'message', message })
  }
  flush()
  return rows
}

export function mergeAssistantRun(messages: AssistantMessage[]): AssistantMessage {
  if (messages.length === 1) return messages[0]
  const first = messages[0]
  const last = messages[messages.length - 1]
  return {
    ...last,
    content: messages.flatMap((message) => message.content),
    timestamp: first.timestamp ?? last.timestamp,
  }
}
