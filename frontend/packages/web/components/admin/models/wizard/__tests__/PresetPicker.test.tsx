import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '@cubebox/core'
import * as core from '@cubebox/core'
import en from '../../../../../messages/en.json'
import { PresetPicker } from '../PresetPicker'
import { makePreset } from './fixtures'

vi.mock('@cubebox/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return { ...actual, listPresets: vi.fn() }
})

// ProviderLogo pulls @lobehub/icons, whose transitive @lobehub/ui has an ESM
// resolution bug under vitest. Stub it — the picker test cares about behavior,
// not the brand glyph.
vi.mock('@/components/admin/models/ProviderLogo', () => ({
  ProviderLogo: () => <div data-testid="provider-logo" aria-hidden />,
}))

const fakeClient = {} as ApiClient

function renderPicker(onPick = vi.fn()): { onPick: ReturnType<typeof vi.fn> } {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <PresetPicker client={fakeClient} selectedSlug={null} onPick={onPick} />
    </NextIntlClientProvider>,
  )
  return { onPick }
}

const anthropic = makePreset({ slug: 'anthropic', display_name: 'Anthropic', category: 'saas' })
const ollama = makePreset({
  slug: 'ollama',
  display_name: 'Ollama',
  short_name: 'Ollama',
  category: 'oss-framework',
  description: 'Local Ollama server.',
  logo: 'ollama',
  api: 'openai-completions',
  auth: { mode: 'none' },
  capability: {},
})

describe('PresetPicker', () => {
  beforeEach(() => {
    vi.mocked(core.listPresets).mockResolvedValue([anthropic, ollama])
  })

  it('renders a card per preset from listPresets', async () => {
    renderPicker()
    expect(await screen.findByText('Anthropic')).toBeInTheDocument()
    expect(screen.getByText('Ollama')).toBeInTheDocument()
    expect(core.listPresets).toHaveBeenCalledWith(fakeClient)
  })

  it('calls onPick when a card is clicked', async () => {
    const { onPick } = renderPicker()
    const card = await screen.findByRole('button', { name: /Anthropic/ })
    fireEvent.click(card)
    expect(onPick).toHaveBeenCalledWith(anthropic)
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

  it('marks the selected preset', async () => {
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <PresetPicker client={fakeClient} selectedSlug="anthropic" onPick={vi.fn()} />
      </NextIntlClientProvider>,
    )
    const card = await screen.findByRole('button', { name: /Anthropic/ })
    expect(within(card).queryByText('Anthropic')).toBeInTheDocument()
    expect(card).toHaveAttribute('aria-pressed', 'true')
  })
})
