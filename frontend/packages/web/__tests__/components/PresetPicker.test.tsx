import { render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import en from '../../messages/en.json'

import { PresetPicker } from '@/components/chat/PresetPicker'
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

beforeEach(() => {
  localStorage.clear()
  clearAllPresetSelectionStores()
})

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  clearAllPresetSelectionStores()
})

describe('PresetPicker', () => {
  it('fetches the workspace preset list on mount and stores it', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        presets: [
          { label: 'main', is_default: true },
          { label: 'mini', is_default: false },
        ],
      }),
    )

    renderWithIntl(<PresetPicker wsId="ws_fetch" />)

    expect(fetchSpy).toHaveBeenCalledWith('/api/v1/ws/ws_fetch/model-presets', {
      credentials: 'include',
    })
    await waitFor(() => {
      expect(getPresetSelectionStore('ws_fetch').getState().presets).toHaveLength(2)
    })
  })

  it('renders the trigger with the persisted label as the selected value', async () => {
    // Persist a known label before render.
    getPresetSelectionStore('ws_persist').getState().setPresetLabel('mini')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        presets: [
          { label: 'main', is_default: true },
          { label: 'mini', is_default: false },
        ],
      }),
    )

    renderWithIntl(<PresetPicker wsId="ws_persist" />)

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_persist').getState().presets).toHaveLength(2)
    })
    // Persisted label remained valid after mount-time validation.
    expect(getPresetSelectionStore('ws_persist').getState().presetLabel).toBe('mini')
  })

  it('resets a stale persisted label to null when missing from fresh list', async () => {
    getPresetSelectionStore('ws_stale').getState().setPresetLabel('ghost')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        presets: [{ label: 'main', is_default: true }],
      }),
    )

    renderWithIntl(<PresetPicker wsId="ws_stale" />)

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_stale').getState().presetLabel).toBeNull()
    })
  })

  it('keeps the selection null when fetch fails (composer falls back to default)', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))

    renderWithIntl(<PresetPicker wsId="ws_offline" />)

    await waitFor(() => {
      // Trigger renders with placeholder (no value).
      expect(screen.getByRole('combobox', { name: 'Model preset' })).toBeInTheDocument()
    })
    expect(getPresetSelectionStore('ws_offline').getState().presetLabel).toBeNull()
  })
})
