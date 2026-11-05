import { describe, expect, it } from 'vitest'
import type { Message } from '@cubeplex/core'
import { backgroundResultFromMessage } from './backgroundResultMessage'

const notice = {
  source: 'background_task',
  notice_id: 'bge-1',
  task_id: 'task-1',
  task_kind: 'command',
  originating_run_id: 'run-0',
  execution_generation: 2,
  reason: 'completion',
  summary: 'build finished',
  result_ref: 'logs/out',
}

function message(overrides: Partial<Message> = {}): Message {
  return {
    id: 'msg-bg',
    role: 'user',
    content: [{ type: 'text', text: `Background task result:\n${JSON.stringify(notice)}` }],
    timestamp: Date.parse('2026-09-22T01:00:00Z') / 1000,
    metadata: { source: 'background_task', notice_id: 'bge-1' },
    ...overrides,
  } as Message
}

describe('backgroundResultFromMessage', () => {
  it('builds a delivered card from the checkpoint notice', () => {
    expect(backgroundResultFromMessage(message())).toMatchObject({
      id: 'bge-1',
      task_id: 'task-1',
      task_kind: 'command',
      execution_generation: 2,
      reason: 'completion',
      summary: 'build finished',
      result_ref: 'logs/out',
      state: 'delivered',
    })
  })

  it('ignores ordinary user messages', () => {
    expect(
      backgroundResultFromMessage(
        message({ metadata: undefined, content: [{ type: 'text', text: 'hi' }] }),
      ),
    ).toBeNull()
  })
})
