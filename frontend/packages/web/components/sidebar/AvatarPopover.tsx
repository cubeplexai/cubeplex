'use client'

import { useRouter } from 'next/navigation'
import { useTheme } from 'next-themes'
import { useEffect, useState } from 'react'
import {
  createApiClient,
  logoutUser,
  useAuthStore,
  useConversationStore,
  useWorkspaceStore,
} from '@cubebox/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useAdminAccess } from '@/hooks/useAdminAccess'
import { LogOut, Moon, Shield, Sun } from 'lucide-react'

export function AvatarPopover() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const { isAdmin } = useAdminAccess()
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  const initials = user?.email ? user.email[0]?.toUpperCase() : '?'

  const onLogout = async () => {
    const client = createApiClient('')
    try {
      await logoutUser(client)
    } catch {
      /* ignore */
    }
    useAuthStore.setState({ user: null })
    useConversationStore.setState({ conversations: [], activeId: null })
    useWorkspaceStore.getState().reset()
    router.replace('/login')
  }

  return (
    <Popover>
      <PopoverTrigger
        aria-label="Account menu"
        className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-accent/60 transition-colors group"
      >
        <div className="size-7 rounded-full bg-gradient-to-br from-primary to-primary/70 text-primary-foreground flex items-center justify-center text-[11px] font-semibold shrink-0 ring-1 ring-primary/20 shadow-sm">
          {initials}
        </div>
        <span className="text-[12.5px] truncate flex-1 text-left text-foreground/90">
          {user?.email ?? '...'}
        </span>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        className="w-56 p-1 shadow-lg border-border/80"
      >
        <div className="px-2 py-2 text-[11px] text-muted-foreground border-b border-border/60 mb-1 truncate">
          {user?.email}
        </div>

        {isAdmin && (
          <a
            href="/admin"
            target="_blank"
            rel="noopener"
            role="menuitem"
            className="flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
          >
            <Shield className="size-3.5 text-muted-foreground" />
            <span>管理后台</span>
          </a>
        )}

        {mounted && (
          <button
            type="button"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
          >
            {theme === 'dark' ? (
              <Sun className="size-3.5 text-muted-foreground" />
            ) : (
              <Moon className="size-3.5 text-muted-foreground" />
            )}
            <span>{theme === 'dark' ? '浅色主题' : '深色主题'}</span>
          </button>
        )}

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
