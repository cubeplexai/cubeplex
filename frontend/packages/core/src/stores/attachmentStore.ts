import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import type { AttachmentDto } from '../types/attachment'
import { deleteAttachment, listAttachments, uploadAttachment } from '../api/attachments'

export interface UploadingFile {
  tempId: string
  filename: string
  size: number
  progress: number
  status: 'uploading' | 'done' | 'error'
  serverFile?: AttachmentDto
  error?: string
  errorCode?: string
}

interface AttachmentStoreState {
  staging: Record<string, UploadingFile[]>
  // Convs whose next hydrate() call should be skipped. Set by clear() callers
  // (home-page submit) that navigate immediately without awaiting send(), so
  // the server's mark_attached_bulk hasn't run yet when the conversation page
  // mounts and fires hydrate() — without this guard the files reappear in the
  // input bar even though they were just submitted.
  skipHydrate: Record<string, true>

  upload(client: ApiClient, convId: string, files: File[]): Promise<void>
  cancel(convId: string, tempId: string): Promise<void>
  remove(client: ApiClient, convId: string, tempId: string): Promise<void>
  clear(convId: string): void
  markSkipHydrate(convId: string): void
  attachedIds(convId: string): string[]
  hydrate(client: ApiClient, convId: string): Promise<void>
}

const newTempId = (): string => `tmp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`

const abortControllers: Record<string, AbortController> = {}

export const useAttachmentStore = create<AttachmentStoreState>((set, get) => ({
  staging: {},
  skipHydrate: {},

  async upload(client, convId, files) {
    const next: UploadingFile[] = files.map((f) => ({
      tempId: newTempId(),
      filename: f.name,
      size: f.size,
      progress: 0,
      status: 'uploading',
    }))
    for (const item of next) abortControllers[item.tempId] = new AbortController()
    set((s) => ({
      staging: {
        ...s.staging,
        [convId]: [...(s.staging[convId] || []), ...next],
      },
    }))

    await Promise.all(
      next.map(async (item, idx) => {
        const controller = abortControllers[item.tempId]
        try {
          const dto = await uploadAttachment(
            client,
            convId,
            files[idx],
            (p) => {
              set((s) => {
                const list = (s.staging[convId] || []).map((u) =>
                  u.tempId === item.tempId ? { ...u, progress: p } : u,
                )
                return { staging: { ...s.staging, [convId]: list } }
              })
            },
            controller?.signal,
          )
          set((s) => {
            const list = (s.staging[convId] || []).map((u) =>
              u.tempId === item.tempId
                ? { ...u, progress: 1, status: 'done' as const, serverFile: dto }
                : u,
            )
            return { staging: { ...s.staging, [convId]: list } }
          })
        } catch (err) {
          const aborted = (err as Error)?.name === 'AbortError'
          if (aborted) {
            set((s) => {
              const list = (s.staging[convId] || []).filter((u) => u.tempId !== item.tempId)
              return { staging: { ...s.staging, [convId]: list } }
            })
          } else {
            const uploadError = err as Error & { errorCode?: string }
            set((s) => {
              const list = (s.staging[convId] || []).map((u) =>
                u.tempId === item.tempId
                  ? {
                      ...u,
                      status: 'error' as const,
                      error: uploadError.message || String(err),
                      errorCode: uploadError.errorCode,
                    }
                  : u,
              )
              return { staging: { ...s.staging, [convId]: list } }
            })
          }
        } finally {
          delete abortControllers[item.tempId]
        }
      }),
    )
  },

  async cancel(convId, tempId) {
    const controller = abortControllers[tempId]
    if (controller) {
      controller.abort()
      delete abortControllers[tempId]
    }
    set((s) => {
      const list = (s.staging[convId] || []).filter((u) => u.tempId !== tempId)
      return { staging: { ...s.staging, [convId]: list } }
    })
  },

  async remove(client, convId, tempId) {
    const item = (get().staging[convId] || []).find((u) => u.tempId === tempId)
    if (item?.serverFile) {
      try {
        await deleteAttachment(client, convId, item.serverFile.id)
      } catch {
        // best-effort — orphan reaper will clean it up server-side
      }
    }
    set((s) => {
      const list = (s.staging[convId] || []).filter((u) => u.tempId !== tempId)
      return { staging: { ...s.staging, [convId]: list } }
    })
  },

  clear(convId) {
    set((s) => {
      const next = { ...s.staging }
      delete next[convId]
      return { staging: next }
    })
  },

  markSkipHydrate(convId) {
    set((s) => ({ skipHydrate: { ...s.skipHydrate, [convId]: true } }))
  },

  attachedIds(convId) {
    return (get().staging[convId] || [])
      .filter((u) => u.status === 'done' && u.serverFile)
      .map((u) => u.serverFile!.id)
  },

  async hydrate(client, convId) {
    // Skip once if the caller set the flag (home-page submit navigates before
    // send() completes, so mark_attached_bulk may not have run yet on the
    // server — we'd re-surface files that were just submitted).
    if (get().skipHydrate[convId]) {
      set((s) => {
        const next = { ...s.skipHydrate }
        delete next[convId]
        return { skipHydrate: next }
      })
      return
    }
    let list: Awaited<ReturnType<typeof listAttachments>>
    try {
      list = await listAttachments(client, convId, 'pending')
    } catch {
      return
    }
    if (!list.attachments.length) return
    set((s) => ({
      staging: {
        ...s.staging,
        [convId]: list.attachments.map((a) => ({
          tempId: newTempId(),
          filename: a.filename,
          size: a.size_bytes,
          progress: 1,
          status: 'done' as const,
          serverFile: a,
        })),
      },
    }))
  },
}))
