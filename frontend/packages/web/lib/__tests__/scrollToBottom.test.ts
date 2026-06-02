import { describe, it, expect } from 'vitest'
import { rafThrottleScrollToBottom } from '../scrollToBottom'

function nextFrame(): Promise<void> {
  return new Promise((r) => requestAnimationFrame(() => r()))
}

describe('rafThrottleScrollToBottom', () => {
  it('coalesces many calls into a single scrollTop write per frame', async () => {
    const el = { scrollTop: 0, scrollHeight: 500 } as unknown as HTMLElement
    const scheduler = rafThrottleScrollToBottom(() => el)

    for (let i = 0; i < 50; i++) scheduler()
    // No write yet — still inside the same task.
    expect(el.scrollTop).toBe(0)

    await nextFrame()
    expect(el.scrollTop).toBe(500)

    // A subsequent burst schedules again and writes the latest scrollHeight.
    ;(el as { scrollHeight: number }).scrollHeight = 800
    for (let i = 0; i < 30; i++) scheduler()
    await nextFrame()
    expect(el.scrollTop).toBe(800)
  })

  it('is a no-op when the element getter returns null', async () => {
    const scheduler = rafThrottleScrollToBottom(() => null)
    expect(() => scheduler()).not.toThrow()
    await nextFrame()
  })

  it('skips the write when the predicate returns false at rAF time', async () => {
    const el = { scrollTop: 0, scrollHeight: 500 } as unknown as HTMLElement
    let stuck = true
    const scheduler = rafThrottleScrollToBottom(
      () => el,
      () => stuck,
    )

    scheduler()
    // Simulate the user scrolling away between schedule-time and rAF-fire.
    stuck = false
    await nextFrame()
    expect(el.scrollTop).toBe(0)

    // Flipping back to stuck and rescheduling does write.
    stuck = true
    scheduler()
    await nextFrame()
    expect(el.scrollTop).toBe(500)
  })
})
