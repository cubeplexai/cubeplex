import type { ApiClient } from './client'
import { toApiError } from './client'
import { CSRF_COOKIE_NAME } from './cookieNames'
import type { AttachmentDto, AttachmentListDto, AttachmentStatus } from '../types/attachment'

const base = (convId: string): string => `/api/v1/conversations/${convId}/attachments`

export async function uploadAttachment(
  client: ApiClient,
  conversationId: string,
  file: File,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<AttachmentDto> {
  const url = client.resolvePath(base(conversationId))
  const fd = new FormData()
  fd.append('file', file)

  return new Promise<AttachmentDto>((resolve, reject) => {
    if (signal?.aborted) {
      const err = new Error('aborted') as Error & { name: string }
      err.name = 'AbortError'
      reject(err)
      return
    }
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.withCredentials = true
    const csrf = document.cookie
      .split('; ')
      .find((c) => c.startsWith(`${CSRF_COOKIE_NAME}=`))
      ?.split('=')[1]
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', decodeURIComponent(csrf))

    const abortHandler = () => xhr.abort()
    signal?.addEventListener('abort', abortHandler)
    const cleanup = () => signal?.removeEventListener('abort', abortHandler)

    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable && onProgress) onProgress(ev.loaded / ev.total)
    }
    xhr.onload = () => {
      cleanup()
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText))
        } catch (e) {
          reject(e instanceof Error ? e : new Error(String(e)))
        }
      } else {
        try {
          const body = JSON.parse(xhr.responseText)
          reject(new Error(body.message || body.detail || `HTTP ${xhr.status}`))
        } catch {
          reject(new Error(`HTTP ${xhr.status}`))
        }
      }
    }
    xhr.onerror = () => {
      cleanup()
      reject(new Error('Network error'))
    }
    xhr.onabort = () => {
      cleanup()
      const err = new Error('aborted') as Error & { name: string }
      err.name = 'AbortError'
      reject(err)
    }
    xhr.send(fd)
  })
}

export async function listAttachments(
  client: ApiClient,
  conversationId: string,
  status: AttachmentStatus | 'all' = 'all',
): Promise<AttachmentListDto> {
  const url = `${base(conversationId)}?status=${status}`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<AttachmentListDto>
}

export async function deleteAttachment(
  client: ApiClient,
  conversationId: string,
  attachmentId: string,
): Promise<void> {
  const res = await client.del(`${base(conversationId)}/${attachmentId}`)
  if (!res.ok) throw await toApiError(res)
}
