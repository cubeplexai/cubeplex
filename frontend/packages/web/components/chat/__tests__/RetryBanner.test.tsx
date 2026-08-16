import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { RetryBanner } from '../RetryBanner'
import type { RetryEvent } from '@/lib/types/events'

function makeEvent(overrides: Partial<RetryEvent['data']> = {}): RetryEvent {
  return {
    type: 'model_retry',
    timestamp: '2026-08-16T12:00:00Z',
    agent_id: null,
    data: {
      model_ref: 'ark/glm-5.2',
      reason: 'simulated 429',
      attempt: 2,
      wait_s: 5,
      ...overrides,
    },
  }
}

describe('RetryBanner', () => {
  it('renders model, attempt, and wait when wait_s > 0', () => {
    render(<RetryBanner event={makeEvent()} />)
    expect(screen.getByText('Retrying ark/glm-5.2 (attempt 2, waiting 5s)')).toBeInTheDocument()
    expect(screen.getByText('simulated 429')).toBeInTheDocument()
  })

  it('omits the wait clause when wait_s is 0', () => {
    render(<RetryBanner event={makeEvent({ wait_s: 0 })} />)
    expect(screen.getByText('Retrying ark/glm-5.2 (attempt 2)')).toBeInTheDocument()
  })
})
