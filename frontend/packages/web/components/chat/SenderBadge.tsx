interface SenderBadgeProps {
  userId: string
  displayName: string
}

export function SenderBadge({ displayName }: SenderBadgeProps) {
  const initial = displayName.trim()[0]?.toUpperCase() ?? '?'
  return (
    <div className="flex items-center justify-end gap-1.5 mb-1">
      <span className="text-xs text-muted-foreground truncate max-w-[60%]">{displayName}</span>
      <div className="size-6 rounded bg-gradient-to-br from-primary to-primary/70 text-primary-foreground flex items-center justify-center text-2xs font-semibold shrink-0">
        {initial}
      </div>
    </div>
  )
}
