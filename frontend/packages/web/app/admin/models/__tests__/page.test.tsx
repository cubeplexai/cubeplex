import { render, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '@cubeplex/core'
import * as core from '@cubeplex/core'
import en from '../../../../messages/en.json'
import ModelsPage from '../page'

// ProviderLogo transitively imports @lobehub/icons which breaks under vitest.
vi.mock('@/components/admin/models/ProviderLogo', () => ({
  ProviderLogo: () => null,
}))

// The page uses useRouter() (Add provider → /admin/models/new); stub it.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

const fakeClient = {
  get: vi.fn(async () => ({ ok: true, json: async () => [] })),
} as unknown as ApiClient

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof core>()
  return {
    ...actual,
    createApiClient: vi.fn(() => fakeClient),
    listPresets: vi.fn(async () => []),
  }
})

function renderPage() {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ModelsPage />
    </NextIntlClientProvider>,
  )
}

describe('admin models page', () => {
  beforeEach(() => {
    vi.mocked(core.listPresets).mockClear()
    vi.mocked(fakeClient.get).mockClear()
  })

  it('reads configured providers (GET /providers) and never lists presets', async () => {
    renderPage()
    await waitFor(() => expect(fakeClient.get).toHaveBeenCalled())
    const paths = vi.mocked(fakeClient.get).mock.calls.map((c) => String(c[0]))
    expect(paths.some((p) => p.includes('/providers'))).toBe(true)
    expect(paths.some((p) => p.includes('presets'))).toBe(false)
    expect(core.listPresets).not.toHaveBeenCalled()
  })
})
