import { describe, expect, it } from 'vitest'
import { toolTimingFromDetails } from '../../src/types/message'

describe('toolTimingFromDetails', () => {
  it('parses TimestampMiddleware ISO fields into epoch ms', () => {
    const started = '2026-06-11T08:00:28.285Z'
    const ended = '2026-06-11T08:00:30.285Z'
    expect(
      toolTimingFromDetails({
        tool_started_at: started,
        tool_ended_at: ended,
      }),
    ).toEqual({
      startedAt: Date.parse(started),
      receivedAt: Date.parse(ended),
    })
  })

  it('returns empty when details missing or not an object', () => {
    expect(toolTimingFromDetails(undefined)).toEqual({})
    expect(toolTimingFromDetails(null)).toEqual({})
    expect(toolTimingFromDetails('nope')).toEqual({})
  })

  it('ignores non-string / unparseable timestamps', () => {
    expect(
      toolTimingFromDetails({
        tool_started_at: 123,
        tool_ended_at: 'not-a-date',
      }),
    ).toEqual({
      startedAt: undefined,
      receivedAt: undefined,
    })
  })

  it('accepts one-sided timing', () => {
    const started = '2026-06-11T08:00:28.285Z'
    expect(toolTimingFromDetails({ tool_started_at: started })).toEqual({
      startedAt: Date.parse(started),
      receivedAt: undefined,
    })
  })
})
