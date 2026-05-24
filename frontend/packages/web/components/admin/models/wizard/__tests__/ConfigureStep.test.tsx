import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient, Provider, VendorPreset } from '@cubebox/core'
import * as core from '@cubebox/core'
import en from '../../../../../messages/en.json'
import { ConfigureStep } from '../ConfigureStep'
import { makeVendor } from './fixtures'

vi.mock('@cubebox/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return { ...actual, createProvider: vi.fn(), updateProvider: vi.fn() }
})

const fakeClient = {} as ApiClient

function created(id: string): Provider {
  return { id } as Provider
}

function renderStep(
  vendor: VendorPreset = makeVendor(),
  onProviderCreated = vi.fn(),
  existingProviderId?: string | null,
  onSelectEndpoint = vi.fn(),
) {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ConfigureStep
        client={fakeClient}
        vendor={vendor}
        selectedPresetKey={vendor.endpoints[0].preset_key}
        onSelectEndpoint={onSelectEndpoint}
        existingProviderId={existingProviderId}
        onProviderCreated={onProviderCreated}
      />
    </NextIntlClientProvider>,
  )
  return { onProviderCreated, onSelectEndpoint }
}

// A tiered, multi-region vendor for exercising the endpoint selectors.
const zhipu = makeVendor({
  vendor: 'zhipu',
  display_name: 'Zhipu',
  endpoints: [
    {
      preset_key: 'zhipu/intl/openai-completions/general',
      region: 'intl',
      protocol: 'openai-completions',
      plan: 'general',
      base_url: 'https://api.z.ai/api/paas/v4',
      model_ids: ['glm-5'],
      capability: { supports_tools: true, temperature: { mode: 'free', default: 0.7 } },
    },
    {
      preset_key: 'zhipu/cn/openai-completions/general',
      region: 'cn',
      protocol: 'openai-completions',
      plan: 'general',
      base_url: 'https://open.bigmodel.cn/api/paas/v4',
      model_ids: ['glm-5'],
      capability: { supports_tools: true, temperature: { mode: 'free', default: 0.7 } },
    },
  ],
  models: [
    {
      model_id: 'glm-5',
      display_name: 'GLM-5',
      plan: ['general'],
      context_window: 200000,
      max_tokens: 32768,
      input_modalities: ['text'],
      reasoning: true,
      pricing: { input: 0, output: 0 },
    },
  ],
})

describe('ConfigureStep', () => {
  beforeEach(() => {
    vi.mocked(core.createProvider).mockReset()
    vi.mocked(core.createProvider).mockResolvedValue(created('prv_new'))
    vi.mocked(core.updateProvider).mockReset()
    vi.mocked(core.updateProvider).mockResolvedValue(created('prv_existing'))
  })

  it('creates a provider with the selected endpoint mapped into the body', async () => {
    const { onProviderCreated } = renderStep()

    const next = screen.getByRole('button', { name: 'Next' })
    expect(next).toBeDisabled()
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-123' } })
    expect(next).toBeEnabled()
    fireEvent.click(next)

    await waitFor(() => expect(core.createProvider).toHaveBeenCalled())
    const body = vi.mocked(core.createProvider).mock.calls[0][1]
    expect(body).toMatchObject({
      name: 'Anthropic',
      provider_type: 'anthropic-messages',
      base_url: 'https://api.anthropic.com',
      auth_type: 'api_key',
      api_key: 'sk-123',
      preset_slug: 'anthropic/intl/anthropic-messages',
    })
    // capability is resolved server-side -> not sent from the wizard.
    expect(body.capability).toBeUndefined()
    await waitFor(() => expect(onProviderCreated).toHaveBeenCalledWith('prv_new'))
  })

  it('revisit: updates the existing provider instead of creating a second one', async () => {
    const { onProviderCreated } = renderStep(makeVendor(), vi.fn(), 'prv_existing')
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(core.updateProvider).toHaveBeenCalled())
    expect(vi.mocked(core.updateProvider).mock.calls[0][1]).toBe('prv_existing')
    expect(core.createProvider).not.toHaveBeenCalled()
    await waitFor(() => expect(onProviderCreated).toHaveBeenCalledWith('prv_existing'))
  })

  it('shows a friendly message on a slug conflict instead of a bare HTTP 409', async () => {
    vi.mocked(core.createProvider).mockRejectedValueOnce(
      new core.ApiError('HTTP 409', 409, 'provider_slug_conflict', {
        code: 'provider_slug_conflict',
      }),
    )
    renderStep()
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() =>
      expect(
        screen.getByText(en.adminModels.wizard.configure.errors.provider_slug_conflict),
      ).toBeInTheDocument(),
    )
    expect(screen.queryByText('HTTP 409')).not.toBeInTheDocument()
  })

  it('endpoint selectors drive the composed base_url + preset_key', async () => {
    const { onSelectEndpoint } = renderStep(zhipu)

    // Default endpoint = first (intl) -> its base_url shows in the form.
    const baseUrl = screen.getByLabelText('Base URL') as HTMLInputElement
    expect(baseUrl.value).toBe('https://api.z.ai/api/paas/v4')

    // Switch the Region selector to CN.
    fireEvent.change(screen.getByLabelText('Region'), { target: { value: 'cn' } })
    await waitFor(() =>
      expect(onSelectEndpoint).toHaveBeenCalledWith('zhipu/cn/openai-completions/general'),
    )
    const baseUrlAfter = screen.getByLabelText('Base URL') as HTMLInputElement
    expect(baseUrlAfter.value).toBe('https://open.bigmodel.cn/api/paas/v4')
  })
})
