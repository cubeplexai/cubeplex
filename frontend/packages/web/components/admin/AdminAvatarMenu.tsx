'use client'

import { useRouter } from 'next/navigation'
import { createApiClient, logoutUser, useAuthStore } from '@cubebox/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { LogOut } from 'lucide-react'

export function AdminAvatarMenu() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const initials = user?.email ? user.email[0]?.toUpperCase() : '?'

  const onLogout = async () => {
    const client = createApiClient('')
    try {
      await logoutUser(client)
    } catch {
      /* ignore */
    }
    useAuthStore.getState().reset()
    router.replace('/login')
  }

  return (
    <Popover>
      <PopoverTrigger
        aria-label="Admin account menu"
        className="size-8 rounded-full bg-gradient-to-br from-primary to-primary/70 text-primary-foreground flex items-center justify-center text-xs font-semibold ring-1 ring-primary/20 shadow-sm hover:shadow-md transition-shadow"
      >
        {initials}
      </PopoverTrigger>
      <PopoverContent
        side="bottom"
        align="end"
        sideOffset={6}
        className="w-56 p-1 shadow-lg border-border/80"
      >
        <div className="px-2 py-2 text-[11px] text-muted-foreground border-b border-border/60 mb-1 truncate">
          {user?.email}
        </div>
        <button
          type="button"
          onClick={onLogout}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-destructive/10 text-destructive transition-colors"
        >
          <LogOut className="size-3.5" />
          <span>退出</span>
        </button>
      </PopoverContent>
    </Popover>
  )
}
