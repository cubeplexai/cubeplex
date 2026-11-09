import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  CONVERSATION_NAV_FALLBACK_MS,
  scheduleConversationNavFallback,
} from './conversationNavFallback'

const FROM = '/w/ws-1/conversations/c1'
const TARGET = '/w/ws-1/conversations/c2'
const LATER = '/w/ws-1/conversations/c3'

function fakeLocation(pathname: string): {
  pathname: string
  assign: (href: string) => void
  assigned: string[]
} {
  const assigned: string[] = []
  return {
    pathname,
    assigned,
    assign(href: string) {
      assigned.push(href)
    },
  }
}

describe('scheduleConversationNavFallback', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it('loads the target when the address is still the page that was clicked from', () => {
    const location = fakeLocation(FROM)

    scheduleConversationNavFallback(TARGET, FROM, location)
    vi.advanceTimersByTime(CONVERSATION_NAV_FALLBACK_MS)

    expect(location.assigned).toEqual([TARGET])
  })

  it('does nothing once the address has moved', () => {
    const location = fakeLocation(FROM)

    scheduleConversationNavFallback(TARGET, FROM, location)
    location.pathname = TARGET
    vi.advanceTimersByTime(CONVERSATION_NAV_FALLBACK_MS)

    expect(location.assigned).toEqual([])
  })

  it('does not reload the conversation that is already open', () => {
    const location = fakeLocation(FROM)

    scheduleConversationNavFallback(FROM, FROM, location)
    vi.advanceTimersByTime(CONVERSATION_NAV_FALLBACK_MS)

    expect(location.assigned).toEqual([])
  })

  it('keeps only the latest click', () => {
    const location = fakeLocation(FROM)

    scheduleConversationNavFallback(TARGET, FROM, location)
    scheduleConversationNavFallback(LATER, FROM, location)
    vi.advanceTimersByTime(CONVERSATION_NAV_FALLBACK_MS)

    expect(location.assigned).toEqual([LATER])
  })

  it('drops a pending fallback when the next click is the open conversation', () => {
    const location = fakeLocation(FROM)

    scheduleConversationNavFallback(TARGET, FROM, location)
    scheduleConversationNavFallback(FROM, FROM, location)
    vi.advanceTimersByTime(CONVERSATION_NAV_FALLBACK_MS)

    expect(location.assigned).toEqual([])
  })

  it('waits out the fallback delay before loading', () => {
    const location = fakeLocation(FROM)

    scheduleConversationNavFallback(TARGET, FROM, location)
    vi.advanceTimersByTime(CONVERSATION_NAV_FALLBACK_MS - 1)

    expect(location.assigned).toEqual([])
  })
})
