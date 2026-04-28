'use client'

import { useAttachmentStore, createApiClient } from '@cubebox/core'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { AttachmentChip } from './AttachmentChip'

interface Props {
  conversationId: string
}

export function AttachmentChips({ conversationId }: Props) {
  const items = useAttachmentStore((s) => s.staging[conversationId] || [])
  const remove = useAttachmentStore((s) => s.remove)
  const { workspaceId } = useWorkspaceContext()

  if (items.length === 0) return null

  const handleRemove = async (tempId: string) => {
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    await remove(client, conversationId, tempId)
  }

  return (
    <div className="flex flex-wrap gap-1.5 pb-2">
      {items.map((item) => (
        <AttachmentChip
          key={item.tempId}
          item={item}
          thumbnailUrl={item.serverFile?.thumbnail_url ?? null}
          onRemove={() => handleRemove(item.tempId)}
        />
      ))}
    </div>
  )
}
