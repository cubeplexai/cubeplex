import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// ModelBrandLogo pulls @lobehub/icons, which breaks under vitest (emoji-mart JSON).
vi.mock('@/components/models/ModelBrandLogo', () => ({
  ModelBrandLogo: ({ label }: { label: string; brand: string | null }) => (
    <span data-testid="model-brand-logo" aria-label={label} />
  ),
}))

import en from '../../messages/en.json'

import { ModelPicker } from '@/components/chat/ModelPicker'
import {
  clearAllPresetSelectionStores,
  getPresetSelectionStore,
} from '@/lib/stores/preset-selection'

function renderWithIntl(ui: React.ReactElement): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  )
}

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

const PRESETS = [
  {
    key: 'pro',
    kind: 'tier' as const,
    primary: 'anthropic/claude-opus-4-7',
    description: '',
    is_default: true,
    provider_slug: 'anthropic',
    model_id: 'claude-opus-4-7',
    model_display_name: 'Claude Opus 4.7',
    context_window: 1_000_000,
    reasoning: true,
    input_modalities: ['text', 'image'],
  },
  {
    key: 'lite',
    kind: 'tier' as const,
    primary: 'openai/gpt-5',
    description: '',
    is_default: false,
    provider_slug: 'openai',
    model_id: 'gpt-5',
    model_display_name: 'GPT-5',
    context_window: 200_000,
    reasoning: false,
    input_modalities: ['text'],
  },
]

beforeEach(() => {
  localStorage.clear()
  clearAllPresetSelectionStores()
})

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  clearAllPresetSelectionStores()
})

describe('ModelPicker', () => {
  it('fetches the workspace preset list on mount and stores it', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ presets: PRESETS }))

    renderWithIntl(<ModelPicker wsId="ws_fetch" />)

    expect(fetchSpy).toHaveBeenCalledWith('/api/v1/ws/ws_fetch/model-presets', {
      credentials: 'include',
    })
    await waitFor(() => {
      expect(getPresetSelectionStore('ws_fetch').getState().presets).toHaveLength(2)
    })
  })

  it('keeps a valid persisted key after mount-time validation', async () => {
    getPresetSelectionStore('ws_persist').getState().setModelKey('lite')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ presets: PRESETS }))

    renderWithIntl(<ModelPicker wsId="ws_persist" />)

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_persist').getState().presets).toHaveLength(2)
    })
    expect(getPresetSelectionStore('ws_persist').getState().modelKey).toBe('lite')
  })

  it('resets a stale persisted key to null when missing from the fresh list', async () => {
    getPresetSelectionStore('ws_stale').getState().setModelKey('ghost')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ presets: [PRESETS[0]] }))

    renderWithIntl(<ModelPicker wsId="ws_stale" />)

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_stale').getState().modelKey).toBeNull()
    })
  })

  it('keeps the selection null when fetch fails (composer falls back to default)', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))

    renderWithIntl(<ModelPicker wsId="ws_offline" />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: en.chat.modelPickerAria })).toBeInTheDocument()
    })
    expect(getPresetSelectionStore('ws_offline').getState().modelKey).toBeNull()
  })

  it('defaults the thinking level to medium', () => {
    expect(getPresetSelectionStore('ws_default').getState().thinking).toBe('medium')
  })

  it('shows tier labels without mono primary or description as list body text', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ presets: PRESETS }))

    renderWithIntl(<ModelPicker wsId="ws_labels" />)

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_labels').getState().presets).toHaveLength(2)
    })

    // Open the popover
    fireEvent.click(screen.getByRole('button', { name: /Model and thinking effort/i }))

    expect(
      screen.getByRole('button', { name: /Pro · anthropic\/claude-opus-4-7/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Lite · openai\/gpt-5/i })).toBeInTheDocument()

    // Visible list should not put full primary as the main text content of the row
    // (primary is in aria-label only). Mono primary string should not appear as standalone text.
    expect(screen.queryByText('anthropic/claude-opus-4-7')).not.toBeInTheDocument()
    // Tier description must not appear as a secondary line in the list
    expect(screen.queryByText(en.adminPresets.modelTiers.pro.description)).not.toBeInTheDocument()
  })

  it('updates selection when a preset row is clicked', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ presets: PRESETS }))

    renderWithIntl(<ModelPicker wsId="ws_click" />)

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_click').getState().presets).toHaveLength(2)
    })

    fireEvent.click(screen.getByRole('button', { name: /Model and thinking effort/i }))
    fireEvent.click(screen.getByRole('button', { name: /Lite · openai\/gpt-5/i }))

    expect(getPresetSelectionStore('ws_click').getState().modelKey).toBe('lite')
  })
})
