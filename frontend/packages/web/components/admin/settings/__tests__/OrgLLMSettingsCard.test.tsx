import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { OrgLLMSettings } from '@cubebox/core'
import * as core from '@cubebox/core'
import en from '../../../../messages/en.json'
import { OrgLLMSettingsCard } from '../OrgLLMSettingsCard'
import type { ProviderModelOption } from '@/hooks/useAllModels'

vi.mock('@cubebox/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return {
    ...actual,
    createApiClient: vi.fn(() => ({}) as core.ApiClient),
    fetchOrgLLMSettings: vi.fn(),
  }
})

const optionsRef: { current: ProviderModelOption[] } = { current: [] }
vi.mock('@/hooks/useAllModels', () => ({
  useAllModels: () => ({
    providers: [],
    options: optionsRef.current,
    loading: false,
    error: null,
  }),
}))

function opt(over: Partial<ProviderModelOption>): ProviderModelOption {
  return {
    providerId: 'prv_1',
    providerName: 'Acme',
    providerSlug: 'acme',
    providerLogoUrl: null,
    modelId: 'm-ready',
    displayName: 'm-ready',
    enabled: true,
    readiness: 'ready',
    ref: 'acme/m-ready',
    ...over,
  }
}

function renderCard() {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <OrgLLMSettingsCard />
    </NextIntlClientProvider>,
  )
}

describe('OrgLLMSettingsCard model picker', () => {
  beforeEach(() => {
    vi.mocked(core.fetchOrgLLMSettings).mockResolvedValue({
      default_model: null,
      fallback_models: [],
    } as unknown as OrgLLMSettings)
    optionsRef.current = [
      opt({ modelId: 'm-ready', ref: 'Acme/m-ready', readiness: 'ready', enabled: true }),
      opt({
        modelId: 'm-broken',
        ref: 'Acme/m-broken',
        readiness: 'provider_error',
        enabled: true,
      }),
    ]
  })

  it('renders an unusable model disabled with its readiness badge, ready one selectable', async () => {
    renderCard()
    const input = await screen.findByPlaceholderText(en.adminSettings.defaultModelPlaceholder)
    // base-ui combobox opens its popup on ArrowDown from the input.
    fireEvent.focus(input)
    fireEvent.keyDown(input, { key: 'ArrowDown', code: 'ArrowDown' })

    const ready = await screen.findByText('m-ready')
    const broken = await screen.findByText('m-broken')

    const readyItem = ready.closest('[role="option"]')
    const brokenItem = broken.closest('[role="option"]')
    expect(readyItem).not.toBeNull()
    expect(brokenItem).not.toBeNull()

    await waitFor(() => expect(brokenItem).toHaveAttribute('aria-disabled', 'true'))
    expect(readyItem).not.toHaveAttribute('aria-disabled', 'true')
    // unusable option surfaces its readiness badge text
    expect(screen.getByText(en.adminModels.readiness.providerError)).toBeInTheDocument()
  })
})
