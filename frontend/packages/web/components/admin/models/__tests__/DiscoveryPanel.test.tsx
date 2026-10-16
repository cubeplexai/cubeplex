import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { afterEach, expect, it, vi } from 'vitest'
import { createApiClient } from '@cubeplex/core'
import { DiscoveryPanel } from '../DiscoveryPanel'
import en from '../../../../messages/en.json'

afterEach(() => vi.unstubAllGlobals())

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
