import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const adminAccess = vi.hoisted(() => ({ isAdmin: false, loading: false }))

// ModelBrandLogo pulls @lobehub/icons, which breaks under vitest (emoji-mart JSON).
vi.mock('@/components/models/ModelBrandLogo', () => ({
  ModelBrandLogo: ({ label }: { label: string; brand: string | null }) => (
    <span data-testid="model-brand-logo" aria-label={label} />
  ),
}))

vi.mock('@/hooks/useAdminAccess', () => ({
  useAdminAccess: () => ({ ...adminAccess, orgId: null, orgName: '', error: undefined }),
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
  adminAccess.isAdmin = false
  adminAccess.loading = false
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

  it('shows an actionable empty state instead of a blank list after a successful empty fetch', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ presets: [] }))
    adminAccess.isAdmin = true

    renderWithIntl(<ModelPicker wsId="ws_empty" />)

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_empty').getState().presetFetchStatus).toBe('ready')
    })
    expect(screen.getByTestId('model-picker-trigger')).toHaveTextContent(
      en.chat.modelPicker.noModel,
    )

    fireEvent.click(screen.getByTestId('model-picker-trigger'))
    expect(screen.getByText(en.chat.modelPicker.emptyTitle)).toBeInTheDocument()
    expect(screen.getByText(en.chat.modelPicker.emptyDescription)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: en.chat.modelPicker.openProviders })).toHaveAttribute(
      'href',
      '/admin/models',
    )
    expect(screen.getByRole('link', { name: en.chat.modelPicker.openPresets })).toHaveAttribute(
      'href',
      '/admin/presets',
    )
  })

  it('shows a visible fetch error and retries instead of silently treating it as empty', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(jsonResponse({ presets: PRESETS }))

    renderWithIntl(<ModelPicker wsId="ws_offline" />)

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_offline').getState().presetFetchStatus).toBe('error')
    })
    fireEvent.click(screen.getByTestId('model-picker-trigger'))
    expect(screen.getByText(en.chat.modelPicker.loadFailedTitle)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: en.chat.modelPicker.retry }))

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_offline').getState().presetFetchStatus).toBe('ready')
    })
    expect(fetchSpy).toHaveBeenCalledTimes(2)
    expect(
      screen.getByRole('button', { name: /Pro · anthropic\/claude-opus-4-7/i }),
    ).toBeInTheDocument()
  })

  it('keeps cached presets selectable when a refresh fails', async () => {
    const store = getPresetSelectionStore('ws_cached')
    store.setState({ presets: PRESETS, presetFetchStatus: 'idle', presetFetchError: null })
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))

    renderWithIntl(<ModelPicker wsId="ws_cached" />)

    await waitFor(() => {
      expect(store.getState().presetFetchStatus).toBe('error')
    })
    fireEvent.click(screen.getByTestId('model-picker-trigger'))

    expect(screen.getByText(en.chat.modelPicker.cachedLoadFailed)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /Pro · anthropic\/claude-opus-4-7/i }),
    ).toBeInTheDocument()
    expect(screen.queryByText(en.chat.modelPicker.emptyTitle)).not.toBeInTheDocument()
  })

  it('asks a non-admin to contact an organization admin for an empty preset list', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ presets: [] }))

    renderWithIntl(<ModelPicker wsId="ws_member_empty" />)

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_member_empty').getState().presetFetchStatus).toBe('ready')
    })
    fireEvent.click(screen.getByTestId('model-picker-trigger'))

    expect(screen.getByText(en.chat.modelPicker.contactAdmin)).toBeInTheDocument()
    expect(
      screen.queryByRole('link', { name: en.chat.modelPicker.openProviders }),
    ).not.toBeInTheDocument()
  })

  it('defaults the thinking level to medium', () => {
    expect(getPresetSelectionStore('ws_default').getState().thinking).toBe('medium')
  })

  it('shows tier labels with model names in the list; no mono primary or description body text', async () => {
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

    // List keeps tier labels; model display names appear as secondary text.
    expect(screen.getByText(en.adminPresets.modelTiers.pro.name)).toBeInTheDocument()
    expect(screen.getByText(en.adminPresets.modelTiers.lite.name)).toBeInTheDocument()
    expect(screen.getByText('GPT-5')).toBeInTheDocument()

    // Visible list should not put full primary as the main text content of the row
    // (primary is in aria-label only). Mono primary string should not appear as standalone text.
    expect(screen.queryByText('anthropic/claude-opus-4-7')).not.toBeInTheDocument()
    // Tier description must not appear as a secondary line in the list
    expect(screen.queryByText(en.adminPresets.modelTiers.pro.description)).not.toBeInTheDocument()
  })

  it('shows the selected model display name on the trigger (not the tier name)', async () => {
    getPresetSelectionStore('ws_trigger').getState().setModelKey('pro')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ presets: PRESETS }))

    renderWithIntl(<ModelPicker wsId="ws_trigger" />)

    await waitFor(() => {
      expect(screen.getByText('Claude Opus 4.7')).toBeInTheDocument()
    })
    // Tier name only appears after opening the list, not on the closed trigger.
    expect(screen.queryByText(en.adminPresets.modelTiers.pro.name)).not.toBeInTheDocument()
  })

  it('persists selection as the tier key, not the model display name', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ presets: PRESETS }))

    renderWithIntl(<ModelPicker wsId="ws_click" />)

    await waitFor(() => {
      expect(getPresetSelectionStore('ws_click').getState().presets).toHaveLength(2)
    })

    fireEvent.click(screen.getByRole('button', { name: /Model and thinking effort/i }))
    fireEvent.click(screen.getByRole('button', { name: /Lite · openai\/gpt-5/i }))

    // modelKey is the preset key (tier) so admin remaps of that tier resolve seamlessly.
    expect(getPresetSelectionStore('ws_click').getState().modelKey).toBe('lite')
  })
})
