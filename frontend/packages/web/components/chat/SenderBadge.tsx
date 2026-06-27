import { Avatar } from '@/components/ui/avatar-resolved'

interface SenderBadgeProps {
  userId: string
  displayName: string
}

export function SenderBadge({ userId, displayName }: SenderBadgeProps) {
  return (
    <div className="flex items-center justify-end gap-1.5 mb-1">
      <span className="text-xs text-muted-foreground truncate max-w-[60%]">{displayName}</span>
      <Avatar seed={userId} name={displayName} userId={userId} size="sm" selfHeal />
    </div>
  )
}
