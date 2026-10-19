import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { afterEach, expect, it, vi } from 'vitest'
import { createApiClient } from '@cubeplex/core'
import { DiscoveryPanel } from '../DiscoveryPanel'
import en from '../../../../messages/en.json'

afterEach(() => vi.unstubAllGlobals())

it('blocks incomplete selections before saving and imports after reviewed limits are applied', async () => {
  const imported = vi.fn()
  const fetcher = vi.fn(async (path: string, init: RequestInit) => {
    if (path.endsWith('/discover'))
      return Response.json({
        status: 'complete',
        discovery_id: 'preview',
        candidates: [
          {
            model_id: 'chat-alias',
            display_name: 'Chat',
            purpose: 'unknown',
            eligible: false,
            missing_fields: ['purpose', 'context_window', 'max_tokens', 'input_modalities'],
            fields: {},
            diagnostics: [],
          },
        ],
        diagnostics: [],
      })
    if (path.endsWith('/providers')) return Response.json({ id: 'prv_created' })
    if (path.endsWith('/models/import')) {
      const body = JSON.parse(init.body as string)
      expect(body.models[0].overrides).toEqual({
        purpose: 'chat',
        input_modalities: ['text'],
        context_window: 8192,
        max_tokens: 512,
      })
      return Response.json({
        results: [{ model_id: 'chat-alias', model_db_id: 'mdl_created', status: 'created' }],
      })
    }
    throw new Error(`Unexpected ${path}`)
  })
  vi.stubGlobal('fetch', fetcher)
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <DiscoveryPanel
        client={createApiClient('')}
        draft={{ name: 'Local', base_url: 'http://localhost:8317/v1', auth_type: 'none' }}
        onImported={imported}
      />
    </NextIntlClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'List available models' }))
  await screen.findByText('1 models returned')
  fireEvent.click(screen.getByRole('button', { name: 'Select all chat candidates' }))
  expect(screen.getByRole('button', { name: 'Import 1 selected models' })).toBeDisabled()
  expect(screen.getByText(/Missing:.*Context window.*Output limit/)).toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(1)
  fireEvent.change(screen.getByLabelText('Context window'), { target: { value: '8192' } })
  fireEvent.change(screen.getByLabelText('Output limit'), { target: { value: '512' } })
  fireEvent.click(screen.getByRole('button', { name: 'Confirm chat/text and apply limits' }))
  expect(screen.getByRole('button', { name: 'Import 1 selected models' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Import 1 selected models' }))
  await waitFor(() => expect(imported).toHaveBeenCalled())
})

it('retries a partial 101-model import in bounded batches without recreating the provider', async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = []
  let failSecondBatch = true
  const candidates = Array.from({ length: 101 }, (_, i) => ({
    model_id: `alias-${i}`,
    display_name: `alias-${i}`,
    purpose: 'unknown',
    fields: {},
    eligible: false,
    missing_fields: ['purpose', 'context_window', 'max_tokens'],
    diagnostics: [],
  }))
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string)
      calls.push({ path, body })
      if (path.endsWith('/discover'))
        return Response.json({
          status: 'complete',
          discovery_id: 'preview',
          availability_source: 'live',
          observed_at: new Date().toISOString(),
          candidates,
          diagnostics: [],
        })
      if (path.endsWith('/providers')) return Response.json({ id: 'prv_created' })
      if (path.endsWith('/models/import')) {
        const models = body.models as { model_id: string }[]
        if (models.length === 1 && failSecondBatch) {
          failSecondBatch = false
          return Response.json(
            { detail: { code: 'temporary', message: 'Retry import' } },
            { status: 503 },
          )
        }
        return Response.json({
          results: models.map((m) => ({
            ...m,
            model_db_id: `mdl_${m.model_id}`,
            status: 'created',
          })),
        })
      }
      throw new Error(`Unexpected ${path}`)
    }),
  )
  const imported = vi.fn()
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <DiscoveryPanel
        client={createApiClient('')}
        draft={{
          name: 'Local',
          provider_type: 'openai-completions',
          base_url: 'http://localhost:8317/v1',
          auth_type: 'none',
        }}
        onImported={imported}
      />
    </NextIntlClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'List available models' }))
  await screen.findByText('101 models returned')
  expect(calls.filter((c) => c.path.endsWith('/providers'))).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: 'Select all chat candidates' }))
  fireEvent.change(screen.getByLabelText('Context window'), { target: { value: '32768' } })
  fireEvent.change(screen.getByLabelText('Output limit'), { target: { value: '1024' } })
  fireEvent.click(screen.getByRole('button', { name: 'Confirm chat/text and apply limits' }))
  fireEvent.click(screen.getByRole('button', { name: 'Import 101 selected models' }))
  await screen.findByRole('alert')
  expect(imported).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Import 101 selected models' }))
  await waitFor(() => expect(imported).toHaveBeenCalled())
  expect(calls.filter((c) => c.path.endsWith('/providers'))).toHaveLength(1)
  const batches = calls.filter((c) => c.path.endsWith('/models/import'))
  expect(batches.map((c) => (c.body.models as unknown[]).length)).toEqual([100, 1, 100, 1])
  const first = (batches[0].body.models as { overrides: Record<string, unknown> }[])[0]
  expect(first.overrides).toEqual({
    purpose: 'chat',
    input_modalities: ['text'],
    context_window: 32768,
    max_tokens: 1024,
  })
  expect(first.overrides).not.toHaveProperty('cost_input')
})

const evidence = (value: unknown, state = 'declared', source = 'listing') => ({
  value,
  state,
  source,
  alternatives: [] as {
    value: unknown
    state: string
    source: string
    alternatives: never[]
  }[],
})

it('omits a cleared conflict choice when a reviewed limit is ready to import', async () => {
  const imported = vi.fn()
  let importBody: {
    models: { resolutions?: Record<string, string>; overrides?: unknown }[]
  } | null = null
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit) => {
      if (path.endsWith('/discover'))
        return Response.json({
          status: 'complete',
          discovery_id: 'preview',
          candidates: [
            {
              model_id: 'alias',
              display_name: 'Alias',
              purpose: 'chat',
              eligible: false,
              missing_fields: ['context_window'],
              diagnostics: [],
              fields: {
                purpose: evidence('chat'),
                context_window: {
                  ...evidence(null, 'conflict'),
                  alternatives: [evidence(8192, 'declared', 'instance')],
                },
                max_tokens: evidence(512),
                input_modalities: evidence(['text']),
              },
            },
          ],
          diagnostics: [],
        })
      if (path.endsWith('/providers')) return Response.json({ id: 'prv_created' })
      if (path.endsWith('/models/import')) {
        importBody = JSON.parse(init.body as string)
        return Response.json({
          results: [{ model_id: 'alias', model_db_id: 'mdl_created', status: 'created' }],
        })
      }
      throw new Error(`Unexpected ${path}`)
    }),
  )
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <DiscoveryPanel
        client={createApiClient('')}
        draft={{ name: 'Local', base_url: 'http://localhost:8317/v1', auth_type: 'none' }}
        onImported={imported}
      />
    </NextIntlClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'List available models' }))
  await screen.findByText('1 models returned')
  fireEvent.click(screen.getByRole('checkbox', { name: /alias/ }))
  const details = screen.getByText('Specifications and sources').closest('details')
  expect(details).not.toBeNull()
  act(() => {
    details!.open = true
    details!.dispatchEvent(new Event('toggle', { bubbles: true }))
  })
  const source = await screen.findByRole('combobox', { name: 'Resolve Context window' })
  fireEvent.change(source, { target: { value: 'instance' } })
  fireEvent.change(source, { target: { value: '' } })
  fireEvent.change(screen.getByLabelText('alias Context window'), { target: { value: '4096' } })
  const importButton = screen.getByRole('button', { name: 'Import 1 selected models' })
  expect(importButton).toBeEnabled()
  fireEvent.click(importButton)
  await waitFor(() => expect(imported).toHaveBeenCalled())
  expect(importBody!.models[0].resolutions ?? {}).toEqual({})
  expect(importBody!.models[0].overrides).toMatchObject({ context_window: 4096 })
})

it('drops a selected model when enrichment declares a non-chat purpose', async () => {
  let listed = false
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      if (!path.endsWith('/discover')) throw new Error(`Unexpected ${path}`)
      const idOnly = {
        model_id: 'embed',
        display_name: 'Embed',
        purpose: 'unknown',
        eligible: false,
        missing_fields: ['purpose', 'context_window', 'max_tokens', 'input_modalities'],
        diagnostics: [],
        fields: {},
      }
      const enriched = {
        ...idOnly,
        purpose: 'audio',
        fields: { purpose: evidence('audio') },
      }
      const candidates = listed ? [enriched] : [idOnly]
      listed = true
      return Response.json({
        status: 'complete',
        discovery_id: 'preview',
        candidates,
        diagnostics: [],
      })
    }),
  )
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <DiscoveryPanel
        client={createApiClient('')}
        draft={{ name: 'Local', base_url: 'http://localhost:8317/v1', auth_type: 'none' }}
        onImported={vi.fn()}
      />
    </NextIntlClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'List available models' }))
  await screen.findByText('1 models returned')
  fireEvent.click(screen.getByRole('button', { name: 'Select all chat candidates' }))
  fireEvent.change(screen.getByLabelText('Context window'), { target: { value: '8192' } })
  fireEvent.change(screen.getByLabelText('Output limit'), { target: { value: '512' } })
  fireEvent.click(screen.getByRole('button', { name: 'Confirm chat/text and apply limits' }))
  expect(screen.getByRole('button', { name: 'Import 1 selected models' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Load selected metadata (up to 100)' }))
  await screen.findByText('Not a chat model')
  expect(screen.getByRole('button', { name: 'Import 0 selected models' })).toBeDisabled()
  const checkbox = screen.getByRole('checkbox', { name: /embed/ })
  expect(checkbox).not.toBeChecked()
  expect(checkbox).toHaveAttribute('aria-disabled', 'true')
})
