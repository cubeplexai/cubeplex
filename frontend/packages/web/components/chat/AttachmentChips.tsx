'use client'

import { createApiClient, useAttachmentStore } from '@cubeplex/core'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { FileChip } from './FileChip'

interface Props {
  conversationId: string
}

export function AttachmentChips({ conversationId }: Props): React.ReactElement | null {
  const items = useAttachmentStore((s) => s.staging[conversationId] || [])
  const cancel = useAttachmentStore((s) => s.cancel)
  const remove = useAttachmentStore((s) => s.remove)
  const { workspaceId } = useWorkspaceContext()

  if (items.length === 0) return null

  const removeAttachment = async (tempId: string): Promise<void> => {
    const item = items.find((u) => u.tempId === tempId)
    if (!item) return
    if (item.status === 'uploading') {
      await cancel(conversationId, tempId)
    } else {
      const client = createApiClient('')
      if (workspaceId) client.setWorkspaceId(workspaceId)
      await remove(client, conversationId, tempId)
    }
  }

  return (
    <div className="flex flex-wrap gap-2 pb-2">
      {items.map((item) => (
        <FileChip
          key={item.tempId}
          item={item}
          thumbnailUrl={item.serverFile?.thumbnail_url ?? null}
          onCancel={() => void removeAttachment(item.tempId)}
        />
      ))}
    </div>
  )
}
