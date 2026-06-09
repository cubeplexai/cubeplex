import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { CSRF_COOKIE_NAME } from '@cubebox/core'
import {
  fetchAdminModelPresets,
  putAdminModelPresets,
  fetchWorkspaceModelPresets,
} from '@/lib/api/presets'
import type { AdminModelPresetsBody } from '@/lib/types/presets'

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

describe('presets API client', () => {
  beforeEach(() => {
    document.cookie = `${CSRF_COOKIE_NAME}=test-csrf-token; path=/`
  })

  afterEach(() => {
    vi.restoreAllMocks()
    document.cookie = `${CSRF_COOKIE_NAME}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`
  })

  describe('fetchAdminModelPresets', () => {
    it('GETs /api/v1/admin/model-presets with credentials and returns parsed body', async () => {
      const payload = {
        value: {
          presets: [{ label: 'main', chain: ['claude-haiku'], is_default: true }],
          task_presets: { title: 'main' },
        },
        origin: 'org' as const,
      }
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(payload))

      const result = await fetchAdminModelPresets()

      expect(fetchSpy).toHaveBeenCalledWith('/api/v1/admin/model-presets', {
        credentials: 'include',
      })
      expect(result).toEqual(payload)
    })

    it('handles a null value (no org override)', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ value: null, origin: 'none' }))
      const result = await fetchAdminModelPresets()
      expect(result.value).toBeNull()
      expect(result.origin).toBe('none')
    })

    it('throws a readable error when the response is not ok', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        new Response(JSON.stringify({ detail: 'forbidden' }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      await expect(fetchAdminModelPresets()).rejects.toThrow('forbidden')
    })
  })

  describe('putAdminModelPresets', () => {
    it('PUTs the body with Content-Type and X-CSRF-Token headers', async () => {
      const fetchSpy = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(new Response(null, { status: 204 }))

      const body: AdminModelPresetsBody = {
        presets: [{ label: 'main', chain: ['claude-haiku'], is_default: true }],
        task_presets: { title: 'main' },
      }

      await putAdminModelPresets(body)

      expect(fetchSpy).toHaveBeenCalledTimes(1)
      const [url, init] = fetchSpy.mock.calls[0]
      expect(url).toBe('/api/v1/admin/model-presets')
      expect(init?.method).toBe('PUT')
      expect(init?.credentials).toBe('include')
      const headers = init?.headers as Record<string, string>
      expect(headers['Content-Type']).toBe('application/json')
      expect(headers['X-CSRF-Token']).toBe('test-csrf-token')
      expect(init?.body).toBe(JSON.stringify(body))
    })

    it('accepts task_presets with only a subset of keys', async () => {
      const fetchSpy = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(new Response(null, { status: 204 }))

      const body: AdminModelPresetsBody = {
        presets: [
          { label: 'main', chain: ['claude-haiku'], is_default: true },
          { label: 'fast', chain: ['claude-haiku'], is_default: false },
        ],
        task_presets: { summarize: 'fast' },
      }

      await putAdminModelPresets(body)

      const init = fetchSpy.mock.calls[0][1]
      expect(JSON.parse(init?.body as string)).toEqual(body)
    })

    it('throws a readable error when the response is not ok', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        new Response(JSON.stringify({ detail: { code: 'invalid', reason: 'bad label' } }), {
          status: 422,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      const body: AdminModelPresetsBody = {
        presets: [{ label: 'main', chain: ['claude-haiku'], is_default: true }],
        task_presets: {},
      }
      await expect(putAdminModelPresets(body)).rejects.toThrow(/invalid.*bad label/)
    })
  })

  describe('fetchWorkspaceModelPresets', () => {
    it('GETs the workspace-scoped endpoint and unwraps presets', async () => {
      const presets = [
        { label: 'main', is_default: true },
        { label: 'fast', is_default: false },
      ]
      const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ presets }))

      const result = await fetchWorkspaceModelPresets('ws_abc123')

      expect(fetchSpy).toHaveBeenCalledWith('/api/v1/ws/ws_abc123/model-presets', {
        credentials: 'include',
      })
      expect(result).toEqual(presets)
    })

    it('throws a readable error when the response is not ok', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        new Response(JSON.stringify({ detail: 'not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      await expect(fetchWorkspaceModelPresets('ws_missing')).rejects.toThrow('not found')
    })
  })
})
