import { describe, expect, it, vi } from 'vitest'
import { uploadAttachment } from '../../src/api/attachments'
import { createApiClient } from '../../src/api/client'

class FakeXHR {
  upload = { onprogress: null as ((e: ProgressEvent) => void) | null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  status = 0
  responseText = ''
  withCredentials = false
  open = vi.fn()
  setRequestHeader = vi.fn()
  send = vi.fn()
  abort = vi.fn(() => {
    this.onabort?.()
  })
}

describe('uploadAttachment', () => {
  it('aborts the request when the signal fires', async () => {
    const xhr = new FakeXHR()
    vi.stubGlobal('XMLHttpRequest', function (this: unknown) {
      return xhr
    })
    const client = createApiClient('')
    const file = new File(['x'], 'a.txt')
    const ac = new AbortController()

    const promise = uploadAttachment(client, 'c1', file, undefined, ac.signal)
    ac.abort()
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' })
    expect(xhr.abort).toHaveBeenCalledTimes(1)
    vi.unstubAllGlobals()
  })

  it('reports progress', async () => {
    const xhr = new FakeXHR()
    vi.stubGlobal('XMLHttpRequest', function (this: unknown) {
      return xhr
    })
    const client = createApiClient('')
    const file = new File(['x'], 'a.txt')
    const onProgress = vi.fn()
    const ac = new AbortController()
    const promise = uploadAttachment(client, 'c1', file, onProgress, ac.signal)
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 } as ProgressEvent)
    expect(onProgress).toHaveBeenCalledWith(0.5)
    ac.abort()
    await expect(promise).rejects.toBeDefined()
    vi.unstubAllGlobals()
  })
})
