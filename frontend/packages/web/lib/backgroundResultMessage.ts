import { getTextContent } from '@cubeplex/core'
import type { BackgroundTaskEvent, Message } from '@cubeplex/core'

const NOTICE_PREFIX = 'Background task result:\n'

function stringField(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

/**
 * A checkpoint user row with `source=background_task` is a delivered result.
 * The card is built from that row so it appears with the history, not from
 * the separate event list the header poll maintains.
 */
export function backgroundResultFromMessage(message: Message): BackgroundTaskEvent | null {
  if (message.role !== 'user' || message.metadata?.source !== 'background_task') return null

  const text = getTextContent(message)
  const raw = text.startsWith(NOTICE_PREFIX) ? text.slice(NOTICE_PREFIX.length) : text
  let notice: Record<string, unknown> = {}
  if (raw.startsWith('{')) {
    try {
      const parsed: unknown = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') notice = parsed as Record<string, unknown>
    } catch {
      notice = {}
    }
  }

  const noticeId =
    stringField(message.metadata.notice_id) ?? stringField(notice.notice_id) ?? message.id
  const fromNotice = notice.execution_generation
  const fromMeta = message.metadata.execution_generation
  let generation = 0
  if (typeof fromNotice === 'number') generation = fromNotice
  else if (typeof fromMeta === 'number') generation = fromMeta
  const stamp = message.timestamp != null ? new Date(message.timestamp * 1000).toISOString() : ''

  return {
    id: noticeId,
    task_id: stringField(notice.task_id) ?? '',
    task_kind: stringField(notice.task_kind) ?? '',
    execution_generation: generation,
    reason: stringField(notice.reason) ?? '',
    summary: stringField(notice.summary) ?? '',
    result_ref: stringField(notice.result_ref),
    state: 'delivered',
    discard_reason: null,
    revision: 0,
    created_at: stamp,
    updated_at: stamp,
    delivered_at: stamp || null,
  }
}
