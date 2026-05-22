import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it } from 'vitest'
import en from '../../../../../messages/en.json'
import { ModelTestCard, type ModelTestState } from '../ModelTestCard'

function makeState(overrides: Partial<ModelTestState> = {}): ModelTestState {
  return {
    model_db_id: 'mdl_1',
    display_name: 'Gemma 31B',
    overall: 'warn',
    blocking_failed: false,
    steps: [
      { name: 'temperature', status: 'pass', latency_ms: null, detail: 'accepted', error: null },
      {
        name: 'streaming',
        status: 'warn',
        latency_ms: null,
        detail: 'no SSE chunks observed',
        error: null,
      },
    ],
    ...overrides,
  }
}

function renderCard(state: ModelTestState) {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ModelTestCard state={state} />
    </NextIntlClientProvider>,
  )
}

describe('ModelTestCard', () => {
  it('shows the reason for a degraded (warn) outcome, not just the chips', () => {
    renderCard(makeState())
    expect(screen.getByText(/no SSE chunks observed/)).toBeInTheDocument()
  })

  it('lists a failed step reason', () => {
    renderCard(
      makeState({
        overall: 'fail',
        blocking_failed: true,
        steps: [
          {
            name: 'liveness',
            status: 'fail',
            latency_ms: null,
            detail: '401 unauthorized',
            error: null,
          },
        ],
      }),
    )
    expect(screen.getByText(/401 unauthorized/)).toBeInTheDocument()
  })
})
