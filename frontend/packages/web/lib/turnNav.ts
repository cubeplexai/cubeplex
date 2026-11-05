import { getTextContent, type Message } from '@cubeplex/core'

/** Resting tick. Short and faint so the column stays out of the way. */
export const TURN_TICK_REST_WIDTH_PX = 6
export const TURN_TICK_REST_OPACITY = 0.4

/** Hovered tick. Full extension the pointer is on. */
export const TURN_TICK_HOVER_WIDTH_PX = 16
export const TURN_TICK_HOVER_OPACITY = 1

/** Tick immediately above or below the hovered one: halfway from rest to full. */
export const TURN_TICK_NEAR_WIDTH_PX = 11
export const TURN_TICK_NEAR_OPACITY = 0.4

export const TURN_TICK_EASING = 'cubic-bezier(0.22, 1, 0.36, 1)'

/** Width and opacity only — never `transition: all`. */
export const TURN_TICK_TRANSITION = `width 0.2s ${TURN_TICK_EASING}, opacity 0.2s ${TURN_TICK_EASING}`

/** Tooltip opens only after the pointer rests. Fast passes must not flash a card. */
export const TURN_PREVIEW_SHOW_DELAY_MS = 90
/** Grace so the pointer can move from the tick onto the card before it closes. */
export const TURN_PREVIEW_HIDE_DELAY_MS = 80
export const TURN_PREVIEW_FADE_MS = 200

/** Middle of the 50–100 character preview window. */
export const TURN_PREVIEW_CHARS = 80

export interface TurnNavItem {
  /** User message id. Stable key for the tick and the DOM turn group. */
  id: string
  userText: string
  assistantText: string
  /** Message ids that belong to this turn, including the user message. */
  messageIds: readonly string[]
}

export interface TickPresentation {
  width: number
  opacity: number
}

/**
 * `distance` is how many ticks away from the hovered tick.
 * 0 is the hovered tick, 1 is a physical neighbor, anything else rests.
 */
export function tickPresentation(distance: number | null): TickPresentation {
  if (distance === 0) {
    return { width: TURN_TICK_HOVER_WIDTH_PX, opacity: TURN_TICK_HOVER_OPACITY }
  }
  if (distance === 1) {
    return { width: TURN_TICK_NEAR_WIDTH_PX, opacity: TURN_TICK_NEAR_OPACITY }
  }
  return { width: TURN_TICK_REST_WIDTH_PX, opacity: TURN_TICK_REST_OPACITY }
}

/** One line, truncated to `limit` code points, with `...` when text was cut. */
export function previewSnippet(text: string, limit = TURN_PREVIEW_CHARS): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  if (!flat) return ''
  const chars = Array.from(flat)
  if (chars.length <= limit) return flat
  return `${chars.slice(0, limit).join('')}...`
}

function mergeAssistantPreview(history: string, streaming: string): string {
  const prior = history.trim()
  const live = streaming.trim()
  if (!live) return prior
  if (!prior) return live
  // The live stream is the full in-progress reply, which may already
  // contain the text committed on earlier bubbles of the same turn.
  if (prior.includes(live)) return prior
  if (live.includes(prior)) return live
  return `${prior}\n\n${live}`
}

/**
 * One tick per real user message, paired with the assistant text that
 * follows it until the next real user message. Synthetic user rows
 * (compaction, todo nudges) are not turns. `streamingText` is the
 * in-progress main-agent reply and belongs to the latest turn.
 */
export function buildTurnNavItems(
  messages: readonly Message[],
  streamingText: string | null,
): TurnNavItem[] {
  const turns: TurnNavItem[] = []
  let current: {
    id: string
    userText: string
    assistantParts: string[]
    messageIds: string[]
  } | null = null

  const flush = (): void => {
    if (!current) return
    turns.push({
      id: current.id,
      userText: current.userText,
      assistantText: current.assistantParts.join('\n\n'),
      messageIds: current.messageIds,
    })
    current = null
  }

  for (const msg of messages) {
    if (msg.role === 'user') {
      if (msg.metadata?.synthetic === true) continue
      // A delivered background result is a card in the transcript, not a turn.
      if (msg.metadata?.source === 'background_task') continue
      flush()
      current = {
        id: msg.id,
        userText: getTextContent(msg),
        assistantParts: [],
        messageIds: [msg.id],
      }
      continue
    }
    if (!current) continue
    current.messageIds.push(msg.id)
    if (msg.role === 'assistant') {
      const text = getTextContent(msg).trim()
      if (text) current.assistantParts.push(text)
    }
  }
  flush()

  if (streamingText && turns.length > 0) {
    const last = turns[turns.length - 1]
    last.assistantText = mergeAssistantPreview(last.assistantText, streamingText)
  }
  return turns
}

export function indexTurnMessages(turns: readonly TurnNavItem[]): ReadonlyMap<string, string> {
  const map = new Map<string, string>()
  for (const turn of turns) {
    for (const messageId of turn.messageIds) map.set(messageId, turn.id)
  }
  return map
}

/**
 * Pixels to add to the scroller's scrollTop so the turn lands in view.
 * A turn that fits is centered. A turn taller than the viewport keeps
 * the question near the top instead of centering the middle of the reply.
 */
export function scrollDeltaForTurn(args: {
  firstTop: number
  lastBottom: number
  viewTop: number
  viewHeight: number
}): number {
  const turnHeight = args.lastBottom - args.firstTop
  if (turnHeight <= args.viewHeight) {
    const turnMid = (args.firstTop + args.lastBottom) / 2
    const viewMid = args.viewTop + args.viewHeight / 2
    return turnMid - viewMid
  }
  return args.firstTop - args.viewTop - args.viewHeight * 0.2
}
