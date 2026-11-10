'use client'

import { useAuthStore, useConversationStore, useTopicStore } from '@cubeplex/core'
import { Avatar, type AvatarStyle } from '@/components/ui/avatar-resolved'

interface SenderBadgeProps {
  userId: string
  displayName: string
  conversationId?: string
  topicId?: string | null
}

export function SenderBadge({ userId, displayName, conversationId, topicId }: SenderBadgeProps) {
  const currentUser = useAuthStore((s) => s.user)
  const convParticipant = useConversationStore((s) =>
    conversationId
      ? s.conversationParticipants[conversationId]?.find((p) => p.user_id === userId)
      : undefined,
  )
  const topicParticipant = useTopicStore((s) =>
    topicId ? s.topicParticipants[topicId]?.find((p) => p.user_id === userId) : undefined,
  )

  const isMe = currentUser?.id === userId
  const participant = convParticipant ?? topicParticipant

  const src = isMe ? currentUser?.avatar_url : participant?.avatar_url
  const seed = isMe
    ? (currentUser?.avatar_seed ?? currentUser?.id)
    : (participant?.avatar_seed ?? userId)
  const style = (isMe ? currentUser?.avatar_style : participant?.avatar_style) as
    AvatarStyle | undefined

  return (
    <div className="flex items-center justify-end gap-1.5 mb-1">
      <span className="text-xs text-muted-foreground truncate max-w-[60%]">{displayName}</span>
      <Avatar src={src} seed={seed} style={style} name={displayName} userId={userId} size="sm" />
    </div>
  )
}
