import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient, Model } from '@cubebox/core'
import * as core from '@cubebox/core'
import en from '../../../../../messages/en.json'
import { ModelsStep } from '../ModelsStep'
import { makePreset } from './fixtures'

vi.mock('@cubebox/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return { ...actual, createModel: vi.fn() }
})

const fakeClient = {} as ApiClient

const preset = makePreset({
  default_models: [
    {
      model_id: 'm-a',
      display_name: 'Model A',
      context_window: 100,
      max_tokens: 50,
      input_modalities: ['text'],
      reasoning: true,
    },
    {
      model_id: 'm-b',
      display_name: 'Model B',
      context_window: 200,
      max_tokens: 80,
      input_modalities: ['text', 'image'],
      reasoning: false,
    },
  ],
})

function renderStep(onModelsCreated = vi.fn()) {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ModelsStep
        client={fakeClient}
        preset={preset}
        providerId="prv_1"
        onModelsCreated={onModelsCreated}
      />
    </NextIntlClientProvider>,
  )
  return { onModelsCreated }
}

describe('ModelsStep', () => {
  beforeEach(() => {
    vi.mocked(core.createModel).mockReset()
    let n = 0
    vi.mocked(core.createModel).mockImplementation(async () => {
      n += 1
      return { id: `mdl_${n}` } as Model
    })
  })

  it('renders default models checked by default', () => {
    renderStep()
    expect(screen.getByText('Model A')).toBeInTheDocument()
    expect(screen.getByText('Model B')).toBeInTheDocument()
  })

  it('Next creates a model per checked entry with enabled:false and collects ids', async () => {
    const { onModelsCreated } = renderStep()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(core.createModel).toHaveBeenCalledTimes(2))

    const firstBody = vi.mocked(core.createModel).mock.calls[0]
    expect(firstBody[1]).toBe('prv_1')
    expect(firstBody[2]).toMatchObject({
      model_id: 'm-a',
      display_name: 'Model A',
      context_window: 100,
      max_tokens: 50,
      input_modalities: ['text'],
      reasoning: true,
      enabled: false,
    })
    await waitFor(() =>
      expect(onModelsCreated).toHaveBeenCalledWith([
        { id: 'mdl_1', model_id: 'm-a', display_name: 'Model A' },
        { id: 'mdl_2', model_id: 'm-b', display_name: 'Model B' },
      ]),
    )
  })

  it('unchecking a model excludes it from creation', async () => {
    const { onModelsCreated } = renderStep()
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(core.createModel).toHaveBeenCalledTimes(1))
    expect(vi.mocked(core.createModel).mock.calls[0][2].model_id).toBe('m-a')
    await waitFor(() =>
      expect(onModelsCreated).toHaveBeenCalledWith([
        { id: 'mdl_1', model_id: 'm-a', display_name: 'Model A' },
      ]),
    )
  })

  it('retry after a mid-import failure skips already-created models', async () => {
    let bAttempts = 0
    vi.mocked(core.createModel).mockReset()
    vi.mocked(core.createModel).mockImplementation(async (_c, _pid, body) => {
      if (body.model_id === 'm-b') {
        bAttempts += 1
        if (bAttempts === 1) throw new Error('boom')
        return { id: 'mdl_b' } as Model
      }
      return { id: 'mdl_a' } as Model
    })
    const { onModelsCreated } = renderStep()
    // Attempt 1: m-a succeeds, m-b fails → no completion.
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(core.createModel).toHaveBeenCalledTimes(2))
    expect(onModelsCreated).not.toHaveBeenCalled()
    // Attempt 2: m-a is cached (skipped), only m-b is re-created.
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(onModelsCreated).toHaveBeenCalled())
    const aCalls = vi.mocked(core.createModel).mock.calls.filter((c) => c[2].model_id === 'm-a')
    expect(aCalls).toHaveLength(1) // m-a POSTed once across both attempts
    expect(onModelsCreated).toHaveBeenCalledWith([
      { id: 'mdl_a', model_id: 'm-a', display_name: 'Model A' },
      { id: 'mdl_b', model_id: 'm-b', display_name: 'Model B' },
    ])
  })
})
