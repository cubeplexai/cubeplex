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

  it('loads the initial preset row and the provider→model catalog', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url === '/api/v1/admin/model-presets') {
        return jsonResponse({
          value: { presets: [], task_presets: {} },
          origin: 'none',
        })
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

    await waitFor(() => expect(screen.queryByRole('heading', { level: 1 })).not.toBeNull())
    expect(screen.getByRole('heading', { name: en.adminPresets.title })).toBeTruthy()

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
    return {
      value: {
        presets: [
          { label: 'main', chain: ['openai/gpt-5'], is_default: true },
          { label: 'fast', chain: ['openai/gpt-5-mini'], is_default: false },
        ],
        task_presets: { title: 'fast' },
      },
      origin: 'org',
    }
  }

  it('renders presets and omits unset task_presets keys from PUT body', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ value: initialResponse().value, origin: 'org' }, 200))

    renderWithIntl(
      <PresetEditor
        initial={initialResponse()}
        availableModels={['openai/gpt-5', 'openai/gpt-5-mini']}
      />,
    )

    const inputs = screen.getAllByRole('textbox') as HTMLInputElement[]
    const labelInputs = inputs.filter((i) => i.placeholder === 'main')
    expect(labelInputs.map((i) => i.value)).toEqual(['main', 'fast'])

    const saveBtn = screen.getByRole('button', { name: en.adminPresets.save })
    fireEvent.click(saveBtn)

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
    const putCall = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PUT')
    expect(putCall).toBeDefined()
    const sent = JSON.parse(putCall![1]!.body as string) as {
      task_presets: Record<string, string>
    }
    expect(sent.task_presets).toEqual({ title: 'fast' })
    // compaction + summarize must not be sent as empty strings.
    expect(sent.task_presets.compaction).toBeUndefined()
    expect(sent.task_presets.summarize).toBeUndefined()
  })

  it('highlights missing refs on a 400 broken_preset response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(
        {
          status: 'error',
          error_code: 'broken_preset',
          message: "preset 'main' has missing refs: openai/gpt-5",
          details: "missing_refs=['openai/gpt-5']",
        },
        400,
      ),
    )

    renderWithIntl(
      <PresetEditor
        initial={initialResponse()}
        availableModels={['openai/gpt-5', 'openai/gpt-5-mini']}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: en.adminPresets.save }))

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeNull())
    // The banner mentions the broken_preset hint.
    expect(screen.getByRole('alert').textContent).toContain('Fix the highlighted refs')
    // The offending chain entry shows the "Missing" badge.
    expect(screen.getAllByText(en.adminPresets.missingRefBadge).length).toBeGreaterThan(0)
  })
})
