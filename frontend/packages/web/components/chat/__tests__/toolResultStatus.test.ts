import { describe, expect, it } from 'vitest'
import { toolResultIsRunning } from '../toolResultStatus'

describe('toolResultIsRunning', () => {
  it('is true only for details.status running', () => {
    expect(toolResultIsRunning(null)).toBe(false)
    expect(toolResultIsRunning({ details: { status: 'running' } })).toBe(true)
    expect(toolResultIsRunning({ details: { status: 'exited' } })).toBe(false)
    expect(toolResultIsRunning({ details: {} })).toBe(false)
    expect(toolResultIsRunning({})).toBe(false)
  })
})
