'use client'

import { useRouter, usePathname } from 'next/navigation'
import Link from 'next/link'
import { useTheme } from 'next-themes'
import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  createApiClient,
  logoutUser,
  updateLanguage,
  useAuthStore,
  useConversationStore,
  useWorkspaceStore,
} from '@cubebox/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useAdminAccess } from '@/hooks/useAdminAccess'
import { ArrowLeft, Languages, LogOut, Moon, Shield, Sun } from 'lucide-react'
import { clearAllPresetSelectionStores } from '@/lib/stores/preset-selection'

export function AvatarPopover() {
  const t = useTranslations('avatar')
  const tShell = useTranslations('shellLayout')
  const router = useRouter()
  const pathname = usePathname()
  const inAdminScope = pathname?.startsWith('/admin') ?? false
  const user = useAuthStore((s) => s.user)
  const { isAdmin } = useAdminAccess()
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
    useAuthStore.getState().reset()
    useConversationStore.setState({ conversations: [], activeId: null })
    useWorkspaceStore.getState().reset()
    clearAllPresetSelectionStores()
    router.replace('/login')
  }

  const onLanguageChange = async (lang: 'en' | 'zh') => {
    if (user) {
      const client = createApiClient('')
      await updateLanguage(client, lang)
    }
    document.cookie = `NEXT_LOCALE=${lang}; path=/; SameSite=Lax`
    router.refresh()
  }

  const currentLocale = user?.language ?? 'en'

  return (
    <Popover>
      <PopoverTrigger
        aria-label={tShell('accountMenu')}
        className="w-full min-w-0 flex items-center gap-2 px-2 py-1.5 rounded hover:bg-accent transition-colors duration-fast group"
      >
        <div className="size-7 rounded bg-gradient-to-br from-primary to-primary/70 text-primary-foreground flex items-center justify-center text-2xs font-semibold shrink-0">
          {initials}
        </div>
        <span className="text-xs truncate flex-1 text-left text-foreground">
          {user?.email ?? '...'}
        </span>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        className="w-56 p-1 shadow-lg border-border-strong"
      >
        <div className="px-2 py-2 text-2xs text-muted-foreground border-b border-border mb-1 truncate">
          {user?.email}
        </div>

        {isAdmin && !inAdminScope && (
          <a
            href="/admin"
            target="_blank"
            className="flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
          >
            <Shield className="size-3.5 text-muted-foreground" />
            <span>{t('adminPanel')}</span>
          </a>
        )}
        {inAdminScope && (
          <Link
            href="/"
            className="flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
          >
            <ArrowLeft className="size-3.5 text-muted-foreground" />
            <span>{t('backToApp')}</span>
          </Link>
        )}

        {mounted && (
          <button
            type="button"
            onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
          >
            {resolvedTheme === 'dark' ? (
              <Sun className="size-3.5 text-muted-foreground" />
            ) : (
              <Moon className="size-3.5 text-muted-foreground" />
            )}
            <span>{resolvedTheme === 'dark' ? t('lightTheme') : t('darkTheme')}</span>
          </button>
        )}

        <div className="flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px]">
          <Languages className="size-3.5 text-muted-foreground shrink-0" />
          <span className="text-muted-foreground">{t('language')}</span>
          <div className="ml-auto flex gap-1">
            <button
              type="button"
              onClick={() => onLanguageChange('zh')}
              className={`px-1.5 py-0.5 rounded text-[11px] transition-colors ${
                currentLocale === 'zh'
                  ? 'bg-primary text-primary-foreground'
                  : 'hover:bg-accent/60 text-muted-foreground'
              }`}
            >
              中文
            </button>
            <button
              type="button"
              onClick={() => onLanguageChange('en')}
              className={`px-1.5 py-0.5 rounded text-[11px] transition-colors ${
                currentLocale === 'en'
                  ? 'bg-primary text-primary-foreground'
                  : 'hover:bg-accent/60 text-muted-foreground'
              }`}
            >
              EN
            </button>
          </div>
        </div>

        <button
          type="button"
          aria-label={tShell('signOut')}
          onClick={onLogout}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-destructive/10 text-destructive transition-colors"
        >
          <LogOut className="size-3.5" />
          <span>{t('signOut')}</span>
        </button>
      </PopoverContent>
    </Popover>
  )
}
