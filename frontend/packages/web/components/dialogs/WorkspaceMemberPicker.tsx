'use client'

import { type WsMember } from '@cubebox/core'
import { Checkbox } from '@/components/ui/checkbox'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

interface WorkspaceMemberPickerProps {
  invitable: WsMember[]
  selected: Set<string>
  onToggle: (userId: string) => void
  emptyText: string
}

export function WorkspaceMemberPicker({
  invitable,
  selected,
  onToggle,
  emptyText,
}: WorkspaceMemberPickerProps): React.ReactElement {
  if (invitable.length === 0) {
    return <p className="text-xs text-muted-foreground">{emptyText}</p>
  }
  return (
    <ScrollArea className={cn('max-h-40 rounded-md border border-border bg-background/50')}>
      <ul className="py-1">
        {invitable.map((m) => {
          const checked = selected.has(m.user_id)
          return (
            <li key={m.user_id}>
              <label
                className={cn(
                  'flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm',
                  'hover:bg-accent/50',
                )}
              >
                <Checkbox checked={checked} onCheckedChange={() => onToggle(m.user_id)} />
                <span className="flex-1 truncate">{m.display_name || m.email}</span>
                {m.display_name && (
                  <span className="truncate text-xs text-muted-foreground">{m.email}</span>
                )}
              </label>
            </li>
          )
        })}
      </ul>
    </ScrollArea>
  )
}
