import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '@cubebox/core'
import * as core from '@cubebox/core'

import en from '../../../../messages/en.json'
import { CSRF_COOKIE_NAME } from '@cubebox/core'
import AdminPresetsPage from '../page'
import { PresetEditor } from '../PresetEditor'
import type { AdminModelPresetsResponse } from '@/lib/api/presets'
import type { ModelPresetsConfig } from '@/lib/types/presets'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

const fakeClient = {
  get: vi.fn(),
} as unknown as ApiClient

vi.mock('@cubebox/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return {
    ...actual,
    createApiClient: vi.fn(() => fakeClient),
  }
})

function sampleConfig(): ModelPresetsConfig {
  return {
    tiers: {
      lite: { enabled: true, primary: 'openai/gpt-5', fallbacks: [] },
      flash: { enabled: false, primary: null, fallbacks: [] },
      pro: { enabled: true, primary: 'anthropic/claude-opus-4-7', fallbacks: [] },
      max: { enabled: false, primary: null, fallbacks: [] },
    },
    custom_presets: [],
    default_preset: 'pro',
    task_routing: {},
  }
}

function renderWithIntl(node: React.ReactElement): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {node}
    </NextIntlClientProvider>,
  )
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('AdminPresetsPage (shell)', () => {
  beforeEach(() => {
    vi.mocked(fakeClient.get).mockReset()
    document.cookie = `${CSRF_COOKIE_NAME}=test-csrf; path=/`
  })

  it('loads the tier editor and the provider→model catalog', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/v1/admin/model-presets') {
        return jsonResponse({ value: sampleConfig(), origin: 'org' })
      }
      throw new Error(`unexpected fetch ${url}`)
    })

    vi.mocked(fakeClient.get).mockImplementation((async (path: string) => {
      if (path === '/api/v1/admin/providers') {
        return jsonResponse([
          { id: 'prov_1', slug: 'openai' },
          { id: 'prov_2', slug: 'anthropic' },
        ])
      }
      if (path === '/api/v1/admin/providers/prov_1') {
        return jsonResponse({ id: 'prov_1', slug: 'openai', models: [{ model_id: 'gpt-5' }] })
      }
      if (path === '/api/v1/admin/providers/prov_2') {
        return jsonResponse({
          id: 'prov_2',
          slug: 'anthropic',
          models: [{ model_id: 'claude-opus-4-7' }],
        })
      }
      throw new Error(`unexpected client.get ${path}`)
    }) as unknown as ApiClient['get'])

    renderWithIntl(<AdminPresetsPage />)

    // The editor mounted with data: page heading + the Pro tier row.
    await waitFor(() => expect(screen.queryByRole('heading', { level: 1 })).not.toBeNull())
    expect(screen.getByRole('heading', { name: en.adminPresets.title })).toBeTruthy()
    expect(screen.getByText(en.adminPresets.modelTiers.pro.name)).toBeTruthy()

    // Provider catalog endpoints were hit.
    const calls = vi.mocked(fakeClient.get).mock.calls.map((c) => String(c[0]))
    expect(calls).toContain('/api/v1/admin/providers')
    expect(calls).toContain('/api/v1/admin/providers/prov_1')
  })
})

describe('PresetEditor', () => {
  beforeEach(() => {
    document.cookie = `${CSRF_COOKIE_NAME}=test-csrf; path=/`
  })

  function initialResponse(): AdminModelPresetsResponse {
    return { value: sampleConfig(), origin: 'org' }
  }

  it('renders the tier rows from the loaded config', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ value: sampleConfig(), origin: 'org' }, 200),
    )

    renderWithIntl(
      <PresetEditor
        initial={initialResponse()}
        availableModels={['openai/gpt-5', 'anthropic/claude-opus-4-7']}
      />,
    )

    // All four tier names render.
    expect(screen.getByText(en.adminPresets.modelTiers.lite.name)).toBeTruthy()
    expect(screen.getByText(en.adminPresets.modelTiers.pro.name)).toBeTruthy()
    // The enabled Pro tier exposes its primary ref.
    expect(screen.getByText('anthropic/claude-opus-4-7')).toBeTruthy()
  })

  it('highlights missing refs on a 400 broken_preset response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(
        {
          status: 'error',
          error_code: 'broken_preset',
          message: "preset 'pro' has missing refs: anthropic/claude-opus-4-7",
          details: "missing_refs=['anthropic/claude-opus-4-7']",
        },
        400,
      ),
    )

    renderWithIntl(
      <PresetEditor
        initial={initialResponse()}
        availableModels={['openai/gpt-5', 'anthropic/claude-opus-4-7']}
      />,
    )

    // Disable the Lite tier: dirty + still valid (default 'pro' remains
    // available), so Save is enabled. Then save to trigger the 400.
    const liteSwitch = document.getElementById('tier-enabled-lite')!
    fireEvent.click(liteSwitch)
    fireEvent.click(screen.getByRole('button', { name: en.adminPresets.save }))

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeNull())
    // The banner mentions the broken_preset hint.
    expect(screen.getByRole('alert').textContent).toContain(en.adminPresets.errorBrokenPreset)
    // The offending ref shows the "Missing" badge.
    expect(screen.getAllByText(en.adminPresets.missingRefBadge).length).toBeGreaterThan(0)
  })
})
