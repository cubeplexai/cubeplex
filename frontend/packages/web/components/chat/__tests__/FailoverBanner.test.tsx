import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { FailoverBanner } from '../FailoverBanner'
import type { FailoverEvent } from '@/lib/types/events'

function makeEvent(overrides: Partial<FailoverEvent['data']> = {}): FailoverEvent {
  return {
    type: 'model_failover',
    timestamp: '2026-06-10T12:00:00Z',
    agent_id: null,
    data: {
      failed_ref: 'anthropic/claude-3-5-sonnet',
      next_ref: 'openai/gpt-4o',
      reason: 'rate_limit_exceeded',
      ...overrides,
    },
  }
}

describe('FailoverBanner', () => {
  it('renders "Switched from X to Y" when next_ref is present', () => {
    render(<FailoverBanner event={makeEvent()} />)
    expect(
      screen.getByText('Switched from anthropic/claude-3-5-sonnet to openai/gpt-4o'),
    ).toBeInTheDocument()
    // Reason still rendered inside the collapsible body
    expect(screen.getByText('rate_limit_exceeded')).toBeInTheDocument()
  })

  it('renders "Failover exhausted on X" when next_ref is null and never shows "null"', () => {
    render(<FailoverBanner event={makeEvent({ next_ref: null, reason: 'chain_exhausted' })} />)
    expect(
      screen.getByText('Failover exhausted on anthropic/claude-3-5-sonnet'),
    ).toBeInTheDocument()
    // Hard guard against the regression the plan explicitly calls out: the
    // banner must never render the literal word "null" for the chain-exhausted
    // case. `queryAllByText` with a substring matcher catches it anywhere.
    expect(
      screen.queryAllByText((_content, node) => (node?.textContent ?? '').includes('null')),
    ).toHaveLength(0)
    expect(screen.getByText('chain_exhausted')).toBeInTheDocument()
  })

  it('uses a <details> element so the body is collapsible by default', () => {
    const { container } = render(<FailoverBanner event={makeEvent()} />)
    const details = container.querySelector('details')
    expect(details).not.toBeNull()
    // Default state: closed (HTML default for <details> without the `open` attr).
    expect(details?.hasAttribute('open')).toBe(false)
  })
})
