import { describe, expect, it } from 'vitest'
import { trimHistoryForActiveRun } from '../../src/stores/messageStore'
import type { Message } from '../../src/types'

function user(text: string, ts: number): Message {
  return {
    id: `u-${text}`,
    role: 'user',
    content: [{ type: 'text', text }],
    timestamp: ts,
    metadata: {},
  } as Message
}
function assistant(text: string, ts: number): Message {
  return {
    id: `a-${text}`,
    role: 'assistant',
    content: [{ type: 'text', text }],
    stop_reason: 'stop',
    timestamp: ts,
    metadata: {},
  } as Message
}

describe('trimHistoryForActiveRun with steers', () => {
  it('does not append a duplicate original when a steer follows it in history', () => {
    const history: Message[] = [
      user('original', 1.0),
      assistant('partial', 1.1),
      user('steer one', 1.2),
    ]
    const result = trimHistoryForActiveRun(history, 'run-1', 'original', '1970-01-01T00:00:01.000Z')
    const originals = result.filter(
      (m) => m.role === 'user' && (m.content[0] as { text: string }).text === 'original',
    )
    expect(originals).toHaveLength(1)
    expect(result.some((m) => m.id === 'pending-run-1')).toBe(false)
  })

  it('still appends a pending original when history has no matching user turn', () => {
    const history: Message[] = [user('different', 1.0)]
    const result = trimHistoryForActiveRun(history, 'run-1', 'original', '1970-01-01T00:00:02.000Z')
    expect(result.some((m) => m.id === 'pending-run-1')).toBe(true)
  })
})
