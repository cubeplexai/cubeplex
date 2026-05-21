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
    await waitFor(() => expect(onModelsCreated).toHaveBeenCalledWith(['mdl_1', 'mdl_2']))
  })

  it('unchecking a model excludes it from creation', async () => {
    const { onModelsCreated } = renderStep()
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(core.createModel).toHaveBeenCalledTimes(1))
    expect(vi.mocked(core.createModel).mock.calls[0][2].model_id).toBe('m-a')
    await waitFor(() => expect(onModelsCreated).toHaveBeenCalledWith(['mdl_1']))
  })
})
