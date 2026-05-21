import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient, Provider } from '@cubebox/core'
import * as core from '@cubebox/core'
import en from '../../../../../messages/en.json'
import { ConfigureStep } from '../ConfigureStep'
import { makePreset } from './fixtures'

vi.mock('@cubebox/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return { ...actual, createProvider: vi.fn() }
})

const fakeClient = {} as ApiClient

function created(id: string): Provider {
  return { id } as Provider
}

function renderStep(preset = makePreset(), onProviderCreated = vi.fn()) {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ConfigureStep client={fakeClient} preset={preset} onProviderCreated={onProviderCreated} />
    </NextIntlClientProvider>,
  )
  return { onProviderCreated }
}

describe('ConfigureStep', () => {
  beforeEach(() => {
    vi.mocked(core.createProvider).mockReset()
    vi.mocked(core.createProvider).mockResolvedValue(created('prv_new'))
  })

  it('api_key preset: requires a key, then creates provider with mapped body', async () => {
    const preset = makePreset({ auth: { mode: 'api_key', header_name: 'x-api-key' } })
    const { onProviderCreated } = renderStep(preset)

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
      preset_slug: 'anthropic',
    })
    expect(body.capability).toEqual(preset.capability)
    await waitFor(() => expect(onProviderCreated).toHaveBeenCalledWith('prv_new'))
  })

  it('bearer preset maps auth_type to bearer_token', async () => {
    const preset = makePreset({ slug: 'tgi', auth: { mode: 'bearer' } })
    renderStep(preset)
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'tok' } })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(core.createProvider).toHaveBeenCalled())
    expect(vi.mocked(core.createProvider).mock.calls[0][1].auth_type).toBe('bearer_token')
  })

  it('none preset: no key input, Next enabled, auth_type none', async () => {
    const preset = makePreset({ slug: 'ollama', auth: { mode: 'none' } })
    renderStep(preset)
    expect(screen.queryByLabelText('API key')).not.toBeInTheDocument()
    const next = screen.getByRole('button', { name: 'Next' })
    expect(next).toBeEnabled()
    fireEvent.click(next)
    await waitFor(() => expect(core.createProvider).toHaveBeenCalled())
    expect(vi.mocked(core.createProvider).mock.calls[0][1].auth_type).toBe('none')
  })

  it('oauth preset: Next disabled with unsupported note', () => {
    const preset = makePreset({ slug: 'anthropic-cc', auth: { mode: 'oauth' } })
    renderStep(preset)
    expect(screen.getByText("OAuth/IAM presets aren't supported yet.")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  })
})
