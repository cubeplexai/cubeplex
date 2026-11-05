import { describe, expect, it } from 'vitest'
import type { Message } from '@cubeplex/core'
import {
  TURN_TICK_TRANSITION,
  buildTurnNavItems,
  indexTurnMessages,
  previewSnippet,
  scrollDeltaForTurn,
  tickPresentation,
} from './turnNav'

function user(id: string, text: string, extra?: Partial<Message>): Message {
  return {
    id,
    role: 'user',
    content: [{ type: 'text', text }],
    ...extra,
  } as Message
}

function assistant(id: string, text: string): Message {
  return {
    id,
    role: 'assistant',
    content: [{ type: 'text', text }],
  } as Message
}

describe('turn navigation items', () => {
  it('builds one turn per real user message and keeps the following reply', () => {
    const turns = buildTurnNavItems(
      [
        user('u1', 'first question'),
        assistant('a1', 'first answer'),
        user('u2', 'second question'),
        assistant('a2', 'second answer'),
      ],
      null,
    )

    expect(turns.map((turn) => turn.id)).toEqual(['u1', 'u2'])
    expect(turns[0]).toMatchObject({
      userText: 'first question',
      assistantText: 'first answer',
      messageIds: ['u1', 'a1'],
    })
    expect(turns[1].assistantText).toBe('second answer')
  })

  it('does not start a turn from a delivered background result', () => {
    const turns = buildTurnNavItems(
      [
        user('u1', 'real question'),
        user('bg', 'Background task result:\n{}', {
          metadata: { source: 'background_task', notice_id: 'bge-1' },
        }),
        assistant('a1', 'still the first reply'),
      ],
      null,
    )

    expect(turns).toHaveLength(1)
    expect(turns[0].id).toBe('u1')
    expect(turns[0].assistantText).toBe('still the first reply')
    expect(turns[0].messageIds).not.toContain('bg')
  })

  it('does not start a turn from a synthetic user message', () => {
    const turns = buildTurnNavItems(
      [
        user('u1', 'real question'),
        user('syn', 'todo nudge', { metadata: { synthetic: true } }),
        assistant('a1', 'still the first reply'),
      ],
      null,
    )

    expect(turns).toHaveLength(1)
    expect(turns[0].id).toBe('u1')
    expect(turns[0].assistantText).toBe('still the first reply')
    expect(turns[0].messageIds).not.toContain('syn')
  })

  it('attaches the live stream to the latest turn and does not duplicate it', () => {
    const turns = buildTurnNavItems(
      [user('u1', 'old'), assistant('a1', 'old reply'), user('u2', 'new question')],
      'live reply',
    )

    expect(turns[0].assistantText).toBe('old reply')
    expect(turns[1].assistantText).toBe('live reply')

    const continued = buildTurnNavItems(
      [user('u1', 'question'), assistant('a1', 'partial')],
      'partial and more',
    )
    expect(continued[0].assistantText).toBe('partial and more')

    const same = buildTurnNavItems(
      [user('u1', 'question'), assistant('a1', 'partial and more')],
      'partial',
    )
    expect(same[0].assistantText).toBe('partial and more')
  })

  it('indexes every message in a turn back to that turn', () => {
    const turns = buildTurnNavItems(
      [user('u1', 'q'), assistant('a1', 'a'), { id: 't1', role: 'tool_result' } as Message],
      null,
    )
    const index = indexTurnMessages(turns)
    expect(index.get('u1')).toBe('u1')
    expect(index.get('a1')).toBe('u1')
    expect(index.get('t1')).toBe('u1')
  })
})

describe('preview snippet', () => {
  it('collapses whitespace and cuts at 80 code points with an ellipsis', () => {
    expect(previewSnippet('  hello\n\nworld  ')).toBe('hello world')
    const long = `问${'答'.repeat(90)}`
    const snippet = previewSnippet(long)
    expect(Array.from(snippet.replace(/\.\.\.$/, ''))).toHaveLength(80)
    expect(snippet.endsWith('...')).toBe(true)
    expect(previewSnippet('short')).toBe('short')
  })
})

describe('tick presentation', () => {
  it('extends the hovered tick fully and its neighbors halfway', () => {
    expect(tickPresentation(null)).toEqual({ width: 6, opacity: 0.4 })
    expect(tickPresentation(0)).toEqual({ width: 16, opacity: 1 })
    expect(tickPresentation(1)).toEqual({ width: 11, opacity: 0.4 })
    expect(tickPresentation(2)).toEqual({ width: 6, opacity: 0.4 })
    expect(TURN_TICK_TRANSITION).toBe(
      'width 0.2s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.2s cubic-bezier(0.22, 1, 0.36, 1)',
    )
    expect(TURN_TICK_TRANSITION).not.toContain('all')
  })
})

describe('scrollDeltaForTurn', () => {
  it('centers a turn that fits and keeps a tall question in view', () => {
    expect(
      scrollDeltaForTurn({ firstTop: 100, lastBottom: 200, viewTop: 0, viewHeight: 400 }),
    ).toBe(-50)

    expect(scrollDeltaForTurn({ firstTop: 0, lastBottom: 1000, viewTop: 0, viewHeight: 400 })).toBe(
      -80,
    )
  })
})
