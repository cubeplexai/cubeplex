import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useAttachmentStore } from '../../src/stores/attachmentStore'
import type { AttachmentDto } from '../../src/types/attachment'

const fakeServerDto: AttachmentDto = {
  id: 'srv-1',
  filename: 'a.png',
  kind: 'image',
  mime_type: 'image/png',
  size_bytes: 100,
  width: 10,
  height: 10,
  status: 'pending',
  thumbnail_url: '/t',
  download_url: '/d',
  created_at: '2026-04-28T00:00:00Z',
}

vi.mock('../../src/api/attachments', () => ({
  uploadAttachment: vi.fn(),
  deleteAttachment: vi.fn(),
  listAttachments: vi.fn(),
}))

import { uploadAttachment, deleteAttachment, listAttachments } from '../../src/api/attachments'

describe('attachmentStore', () => {
  beforeEach(() => {
    useAttachmentStore.setState({ staging: {} })
    vi.clearAllMocks()
  })

  it('starts empty', () => {
    expect(useAttachmentStore.getState().staging).toEqual({})
  })

  it('upload appends UploadingFile and replaces with serverFile on resolve', async () => {
    ;(uploadAttachment as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(fakeServerDto)
    const fakeClient = {
      resolvePath: (s: string) => s,
      get: vi.fn(),
      delete: vi.fn(),
    } as unknown
    const fakeFile = new File([new Uint8Array([1, 2, 3])], 'a.png', {
      type: 'image/png',
    })

    const { upload } = useAttachmentStore.getState()
    await upload(fakeClient as never, 'conv1', [fakeFile])

    const staging = useAttachmentStore.getState().staging.conv1
    expect(staging).toBeDefined()
    expect(staging.length).toBe(1)
    expect(staging[0].serverFile?.id).toBe('srv-1')
    expect(useAttachmentStore.getState().attachedIds('conv1')).toEqual(['srv-1'])
  })

  it('clear removes staging for one conv only', () => {
    useAttachmentStore.setState({
      staging: {
        conv1: [{ tempId: 't1', filename: 'x', size: 1, progress: 1, status: 'done' }],
        conv2: [{ tempId: 't2', filename: 'y', size: 1, progress: 1, status: 'done' }],
      },
    })
    useAttachmentStore.getState().clear('conv1')
    expect(useAttachmentStore.getState().staging.conv1).toBeUndefined()
    expect(useAttachmentStore.getState().staging.conv2).toBeDefined()
  })

  it('remove deletes when serverFile present', async () => {
    ;(deleteAttachment as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(undefined)
    useAttachmentStore.setState({
      staging: {
        conv1: [
          {
            tempId: 't1',
            filename: 'x',
            size: 1,
            progress: 1,
            status: 'done',
            serverFile: fakeServerDto,
          },
        ],
      },
    })
    const fakeClient = {} as unknown
    await useAttachmentStore.getState().remove(fakeClient as never, 'conv1', 't1')
    expect(useAttachmentStore.getState().staging.conv1).toEqual([])
    expect(deleteAttachment).toHaveBeenCalledWith(fakeClient, 'conv1', 'srv-1')
  })

  it('hydrate fills staging from server pending list', async () => {
    ;(listAttachments as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      attachments: [fakeServerDto],
      total: 1,
    })
    const fakeClient = {} as unknown
    await useAttachmentStore.getState().hydrate(fakeClient as never, 'conv1')
    const list = useAttachmentStore.getState().staging.conv1
    expect(list?.length).toBe(1)
    expect(list?.[0].serverFile?.id).toBe('srv-1')
  })
})
