import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient, Model } from '@cubeplex/core'
import * as core from '@cubeplex/core'
import en from '../../../../../messages/en.json'
import { ModelsStep } from '../ModelsStep'
import type { CreatedModel } from '../wizardMachine'
import { makeVendor } from './fixtures'

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return { ...actual, createModel: vi.fn() }
})

const fakeClient = {} as ApiClient

const PRESET_KEY = 'v/intl/openai-completions'
const vendor = makeVendor({
  vendor: 'v',
  endpoints: [
    {
      preset_key: PRESET_KEY,
      region: 'intl',
      protocol: 'openai-completions',
      plan: null,
      base_url: 'https://x/v1',
      model_ids: ['m-a', 'm-b'],
      capability: { supports_tools: true },
    },
  ],
  models: [
    {
      model_id: 'm-a',
      display_name: 'Model A',
      plan: null,
      context_window: 100,
      max_tokens: 50,
      input_modalities: ['text'],
      reasoning: true,
      pricing: { input: 0, output: 0 },
    },
    {
      model_id: 'm-b',
      display_name: 'Model B',
      plan: null,
      context_window: 200,
      max_tokens: 80,
      input_modalities: ['text', 'image'],
      reasoning: false,
      pricing: { input: 0, output: 0 },
    },
  ],
})

function renderStep(onModelsCreated = vi.fn(), existingModels: CreatedModel[] = []) {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ModelsStep
        client={fakeClient}
        vendor={vendor}
        presetKey={PRESET_KEY}
        providerId="prv_1"
        existingModels={existingModels}
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

  it('renders endpoint models checked by default', () => {
    renderStep()
    expect(screen.getByText('Model A')).toBeInTheDocument()
    expect(screen.getByText('Model B')).toBeInTheDocument()
  })

  it('adds a custom model via the full form dialog with config + pricing', async () => {
    renderStep()
    fireEvent.click(screen.getByRole('button', { name: en.adminModels.wizard.models.addCustom }))
    // The shared model form dialog opens (same as the management page).
    expect(await screen.findByTestId('model-form-dialog')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('gpt-4o'), { target: { value: 'custom-x' } })
    fireEvent.change(screen.getByPlaceholderText('GPT-4o'), { target: { value: 'Custom X' } })
    fireEvent.change(screen.getByLabelText(en.adminModels.costInput), { target: { value: '1.5' } })
    fireEvent.change(screen.getByLabelText(en.adminModels.contextWindow), {
      target: { value: '64000' },
    })
    fireEvent.click(screen.getByRole('button', { name: en.adminModels.save }))

    // The new row is staged and included on Next, carrying its config + pricing.
    expect(await screen.findByText('Custom X')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => {
      const call = vi.mocked(core.createModel).mock.calls.find((c) => c[2].model_id === 'custom-x')
      expect(call).toBeDefined()
      expect(call?.[2]).toMatchObject({
        model_id: 'custom-x',
        cost_input: 1.5,
        context_window: 64000,
        enabled: false,
      })
    })
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

  it('re-entering after stepping back does not re-create models already persisted', async () => {
    // The wizard unmounts this step on "back" and remounts on "next", passing the
    // previously-created models as existingModels. handleNext must reuse them
    // instead of re-POSTing (which 409s "already exists in this provider").
    const existing: CreatedModel[] = [
      { id: 'mdl_1', model_id: 'm-a', display_name: 'Model A' },
      { id: 'mdl_2', model_id: 'm-b', display_name: 'Model B' },
    ]
    const { onModelsCreated } = renderStep(vi.fn(), existing)
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(onModelsCreated).toHaveBeenCalledWith(existing))
    expect(core.createModel).not.toHaveBeenCalled()
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
