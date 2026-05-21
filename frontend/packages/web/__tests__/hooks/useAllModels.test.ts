import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import * as core from '@cubebox/core'
import type { Model, Provider } from '@cubebox/core'

function makeProvider(over: Partial<Provider> = {}): Provider {
  return {
    id: 'prv_1',
    name: 'My X',
    slug: 'my-x',
    provider_type: 'openai-completions',
    base_url: 'https://api.example.com',
    auth_type: 'api_key',
    has_api_key: true,
    logo_url: null,
    enabled: true,
    is_system: false,
    model_count: 1,
    extra_body: {},
    extra_headers: {},
    created_by_user_id: 'u1',
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...over,
  }
}

function makeModel(over: Partial<Model> = {}): Model {
  return {
    id: 'mdl_1',
    provider_id: 'prv_1',
    model_id: 'm-1',
    display_name: 'M1',
    reasoning: false,
    input_modalities: ['text'],
    cost_input: 0,
    cost_output: 0,
    cost_cache_read: 0,
    cost_cache_write: 0,
    context_window: 8000,
    max_tokens: 1000,
    extra_body: {},
    extra_headers: {},
    enabled: true,
    is_system: false,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...over,
  }
}

describe('useAllModels', () => {
  it('builds ref from provider.slug, not provider.name', async () => {
    const provider = makeProvider({ name: 'My X', slug: 'my-x' })
    const model = makeModel({ model_id: 'm-1' })

    vi.spyOn(core, 'fetchProviders').mockResolvedValue([provider])
    vi.spyOn(core, 'fetchProvider').mockResolvedValue({ ...provider, models: [model] })

    const { useAllModels } = await import('../../hooks/useAllModels')
    const { result } = renderHook(() => useAllModels())

    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.options).toHaveLength(1)
    expect(result.current.options[0].ref).toBe('my-x/m-1')
    expect(result.current.options[0].providerSlug).toBe('my-x')

    vi.restoreAllMocks()
  })
})
