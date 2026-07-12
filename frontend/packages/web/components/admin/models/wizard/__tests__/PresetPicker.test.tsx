import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '@cubeplex/core'
import * as core from '@cubeplex/core'
import en from '../../../../../messages/en.json'
import { PresetPicker } from '../PresetPicker'
import { makeVendor } from './fixtures'

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return { ...actual, listPresets: vi.fn() }
})

// ProviderLogo pulls @lobehub/icons, whose transitive @lobehub/ui has an ESM
// resolution bug under vitest. Stub it — the picker test cares about behavior.
vi.mock('@/components/admin/models/ProviderLogo', () => ({
  ProviderLogo: () => <div data-testid="provider-logo" aria-hidden />,
}))

const fakeClient = {} as ApiClient

function renderPicker(onPickVendor = vi.fn()): { onPickVendor: ReturnType<typeof vi.fn> } {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <PresetPicker client={fakeClient} selectedVendor={null} onPickVendor={onPickVendor} />
    </NextIntlClientProvider>,
  )
  return { onPickVendor }
}

const anthropic = makeVendor({ vendor: 'anthropic', display_name: 'Anthropic', category: 'saas' })
const ollama = makeVendor({
  vendor: 'ollama',
  display_name: 'Ollama',
  short_name: 'Ollama',
  category: 'oss-framework',
  description: 'Local Ollama server.',
  logo: 'ollama',
  endpoints: [
    {
      preset_key: 'ollama/local/openai-completions',
      region: 'local',
      protocol: 'openai-completions',
      plan: null,
      base_url: 'http://localhost:11434/v1',
      model_ids: [],
      capability: {},
    },
  ],
  models: [],
})

describe('PresetPicker', () => {
  beforeEach(() => {
    vi.mocked(core.listPresets).mockResolvedValue([anthropic, ollama])
  })

  it('renders a card per vendor from listPresets', async () => {
    renderPicker()
    expect(await screen.findByText('Anthropic')).toBeInTheDocument()
    expect(screen.getByText('Ollama')).toBeInTheDocument()
    expect(core.listPresets).toHaveBeenCalledWith(fakeClient)
  })

  it('calls onPickVendor when a card is clicked', async () => {
    const { onPickVendor } = renderPicker()
    const card = await screen.findByRole('button', { name: /Anthropic/ })
    fireEvent.click(card)
    expect(onPickVendor).toHaveBeenCalledWith(anthropic)
  })

  it('filters by search query', async () => {
    renderPicker()
    await screen.findByText('Anthropic')
    fireEvent.change(screen.getByLabelText('Search providers'), { target: { value: 'olla' } })
    await waitFor(() => {
      expect(screen.queryByText('Anthropic')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Ollama')).toBeInTheDocument()
  })

  it('filters by category tab', async () => {
    renderPicker()
    await screen.findByText('Anthropic')
    fireEvent.click(screen.getByRole('tab', { name: 'Self-hosted' }))
    await waitFor(() => {
      expect(screen.queryByText('Anthropic')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Ollama')).toBeInTheDocument()
  })

  it('marks the selected vendor', async () => {
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <PresetPicker client={fakeClient} selectedVendor="anthropic" onPickVendor={vi.fn()} />
      </NextIntlClientProvider>,
    )
    const card = await screen.findByRole('button', { name: /Anthropic/ })
    expect(within(card).queryByText('Anthropic')).toBeInTheDocument()
    expect(card).toHaveAttribute('aria-pressed', 'true')
  })
})
