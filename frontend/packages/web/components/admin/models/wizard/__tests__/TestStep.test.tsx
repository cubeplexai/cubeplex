import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient, ProbeResult, ProbeStep, TestStreamEvent } from '@cubeplex/core'
import * as core from '@cubeplex/core'
import en from '../../../../../messages/en.json'
import { TestStep } from '../TestStep'

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return {
    ...actual,
    startTestStream: vi.fn(),
    parseTestStream: vi.fn(),
    enableTestedModels: vi.fn(),
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
    vi.mocked(core.enableTestedModels).mockReset()
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
    vi.mocked(core.enableTestedModels).mockResolvedValue({
      results: [{ model_db_id: 'mdl_1', status: 'enabled' }],
    })
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
      expect(core.enableTestedModels).toHaveBeenCalledWith(fakeClient, 'prv_1', ['mdl_1']),
    )
    await waitFor(() => expect(onFinish).toHaveBeenCalled())
  })

  it('keeps Save disabled when liveness fails', async () => {
    vi.mocked(core.parseTestStream).mockReturnValue(
      streamEvents([
        {
          event: 'liveness',
          data: {
            name: 'liveness',
            status: 'fail',
            detail: '',
            error: {
              type: 'TimeoutError',
              message: 'Model did not finish responding within 30 seconds.',
            },
          },
        },
        { event: 'done', data: {} },
      ]),
    )
    renderStep()
    const save = await screen.findByRole('button', { name: 'Finish' })
    await waitFor(() => expect(core.parseTestStream).toHaveBeenCalled())
    expect(save).toBeDisabled()
    expect(screen.getByText('Model did not finish responding within 30 seconds.')).toBeVisible()
  })

  it('retests only the failed model and retains the other passing result', async () => {
    vi.mocked(core.parseTestStream)
      .mockReturnValueOnce(
        streamEvents([
          { event: 'liveness', data: livenessPass },
          { event: 'model', data: modelPass },
          {
            event: 'model',
            data: { ...modelPass, model_db_id: 'mdl_2', overall: 'fail', blocking_failed: true },
          },
          { event: 'done', data: {} },
        ]),
      )
      .mockReturnValueOnce(
        streamEvents([
          { event: 'liveness', data: livenessPass },
          { event: 'model', data: { ...modelPass, model_db_id: 'mdl_2' } },
          { event: 'done', data: {} },
        ]),
      )
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <TestStep
          client={fakeClient}
          providerId="prv_1"
          modelDbIds={['mdl_1', 'mdl_2']}
          modelLabels={{ mdl_1: 'First', mdl_2: 'Second' }}
          onFinish={vi.fn()}
        />
      </NextIntlClientProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: /^Re-test$/ }))
    await waitFor(() =>
      expect(core.startTestStream).toHaveBeenLastCalledWith(
        fakeClient,
        'prv_1',
        ['mdl_2'],
        expect.any(AbortSignal),
      ),
    )
    expect(screen.getByText('First')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Finish' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Finish' }))
    await waitFor(() =>
      expect(core.enableTestedModels).toHaveBeenCalledWith(fakeClient, 'prv_1', ['mdl_1', 'mdl_2']),
    )
  })
})
