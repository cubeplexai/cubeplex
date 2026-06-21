import { render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
    kind: 'tier',
    primary: 'anthropic/claude-opus-4-7',
    description: '',
    is_default: true,
  },
  { key: 'lite', kind: 'tier', primary: 'openai/gpt-5', description: '', is_default: false },
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
})
