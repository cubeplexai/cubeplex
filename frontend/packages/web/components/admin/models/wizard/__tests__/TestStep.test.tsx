import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient, Model, ProbeResult, ProbeStep, TestStreamEvent } from '@cubeplex/core'
import * as core from '@cubeplex/core'
import en from '../../../../../messages/en.json'
import { TestStep } from '../TestStep'

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return {
    ...actual,
    startTestStream: vi.fn(),
    parseTestStream: vi.fn(),
    setModelEnabled: vi.fn(),
  }
})

const fakeClient = {} as ApiClient

const livenessPass: ProbeStep = { name: 'liveness', status: 'pass', latency_ms: 42 }
const modelPass: ProbeResult & { model_db_id: string } = {
  model_db_id: 'mdl_1',
  overall: 'pass',
  blocking_failed: false,
  steps: [
    { name: 'auth', status: 'pass' },
    { name: 'chat', status: 'pass' },
    { name: 'tools', status: 'pass' },
    { name: 'streaming', status: 'pass' },
    { name: 'usage', status: 'warn' },
  ],
}

function streamEvents(events: TestStreamEvent[]): AsyncGenerator<TestStreamEvent> {
  return (async function* () {
    for (const e of events) yield e
  })()
}

function renderStep(onFinish = vi.fn()) {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <TestStep
        client={fakeClient}
        providerId="prv_1"
        modelDbIds={['mdl_1']}
        modelLabels={{ mdl_1: 'GPT Test' }}
        onFinish={onFinish}
      />
    </NextIntlClientProvider>,
  )
  return { onFinish }
}

describe('TestStep', () => {
  beforeEach(() => {
    vi.mocked(core.startTestStream).mockReset()
    vi.mocked(core.parseTestStream).mockReset()
    vi.mocked(core.setModelEnabled).mockReset()
    vi.mocked(core.startTestStream).mockResolvedValue(
      new ReadableStream<Uint8Array>() as ReadableStream<Uint8Array>,
    )
    vi.mocked(core.parseTestStream).mockReturnValue(
      streamEvents([
        { event: 'liveness', data: livenessPass },
        { event: 'model', data: modelPass },
        { event: 'done', data: {} },
      ]),
    )
    vi.mocked(core.setModelEnabled).mockResolvedValue({ id: 'mdl_1' } as Model)
  })

  it('shows the model display name (not the db id) when the event omits one', async () => {
    renderStep()
    expect(await screen.findByText('GPT Test')).toBeInTheDocument()
    expect(screen.queryByText('mdl_1')).not.toBeInTheDocument()
  })

  it('streams a green liveness row and a card per model, then enables Save', async () => {
    renderStep()
    expect(await screen.findByText('Available')).toBeInTheDocument()
    // liveness row shows latency
    expect(screen.getByText(/42/)).toBeInTheDocument()
    const save = await screen.findByRole('button', { name: 'Finish' })
    await waitFor(() => expect(save).toBeEnabled())
  })

  it('Save enables passing/warn models then calls onFinish', async () => {
    const { onFinish } = renderStep()
    const save = await screen.findByRole('button', { name: 'Finish' })
    await waitFor(() => expect(save).toBeEnabled())
    fireEvent.click(save)
    await waitFor(() =>
      expect(core.setModelEnabled).toHaveBeenCalledWith(fakeClient, 'prv_1', 'mdl_1', true),
    )
    await waitFor(() => expect(onFinish).toHaveBeenCalled())
  })

  it('keeps Save disabled when liveness fails', async () => {
    vi.mocked(core.parseTestStream).mockReturnValue(
      streamEvents([
        {
          event: 'liveness',
          data: { name: 'liveness', status: 'fail', error: { type: 'auth', message: 'bad key' } },
        },
        { event: 'done', data: {} },
      ]),
    )
    renderStep()
    const save = await screen.findByRole('button', { name: 'Finish' })
    await waitFor(() => expect(core.parseTestStream).toHaveBeenCalled())
    expect(save).toBeDisabled()
  })
})
