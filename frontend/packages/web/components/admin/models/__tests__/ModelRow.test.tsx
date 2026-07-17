import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient, Model, ProbeResult } from '@cubeplex/core'
import * as core from '@cubeplex/core'
import en from '../../../../messages/en.json'
import { ModelRow } from '../ModelRow'

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return { ...actual, testModel: vi.fn() }
})

const fakeClient = {} as ApiClient

function makeModel(overrides: Partial<Model> = {}): Model {
  return {
    id: 'mdl_1',
    provider_id: 'prv_1',
    model_id: 'gpt-test',
    display_name: 'GPT Test',
    reasoning: false,
    input_modalities: ['text'],
    cost_input: 0,
    cost_output: 0,
    cost_cache_read: 0,
    cost_cache_write: 0,
    context_window: 128000,
    max_tokens: 4096,
    extra_body: {},
    extra_headers: {},
    enabled: true,
    is_system: false,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

function renderRow(model: Model, onRetested = vi.fn()) {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ModelRow
        model={model}
        client={fakeClient}
        providerId="prv_1"
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onRetested={onRetested}
      />
    </NextIntlClientProvider>,
  )
  return { onRetested }
}

describe('ModelRow', () => {
  beforeEach(() => {
    vi.mocked(core.testModel).mockReset()
    vi.mocked(core.testModel).mockResolvedValue({
      overall: 'pass',
      blocking_failed: false,
      steps: [],
    } as ProbeResult)
  })

  it('renders the readiness badge for a degraded model', () => {
    renderRow(makeModel({ readiness: 'degraded' }))
    expect(screen.getByText(en.adminModels.readiness.degraded)).toBeInTheDocument()
  })

  it('surfaces warn/fail probe-step reasons from last_test_summary', () => {
    renderRow(
      makeModel({
        readiness: 'degraded',
        last_test_summary: {
          overall: 'warn',
          steps: [
            { name: 'temperature', status: 'pass', detail: 'ok' },
            { name: 'streaming', status: 'warn', detail: 'no SSE chunks observed' },
          ],
        },
      }),
    )
    // Issues render as a per-step list (name + cleaned detail).
    const items = screen.getAllByRole('listitem')
    expect(items.some((li) => li.textContent === 'streaming — no SSE chunks observed')).toBe(true)
    // passing steps are not listed as issues
    expect(items.some((li) => li.textContent?.includes('temperature'))).toBe(false)
  })

  it('clicking re-test calls testModel with the provider and model db ids', async () => {
    const { onRetested } = renderRow(makeModel())
    const btn = screen.getByTestId('model-row-gpt-test-retest')
    fireEvent.click(btn)
    await waitFor(() => expect(core.testModel).toHaveBeenCalledWith(fakeClient, 'prv_1', 'mdl_1'))
    await waitFor(() => expect(onRetested).toHaveBeenCalled())
  })
})
