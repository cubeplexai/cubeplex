'use client'

import { useRouter, usePathname } from 'next/navigation'
import Link from 'next/link'
import { useTheme } from 'next-themes'
import { useEffect, useState } from 'react'
import { useLocale, useTranslations } from 'next-intl'
import {
  createApiClient,
  logoutUser,
  updateLanguage,
  useAuthStore,
  useConversationStore,
  useMessageStore,
  useWorkspaceStore,
} from '@cubeplex/core'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Avatar } from '@/components/ui/avatar-resolved'
import { useAdminAccess } from '@/hooks/useAdminAccess'
import {
  ArrowLeft,
  Check,
  ChevronDown,
  Languages,
  LogOut,
  Moon,
  Shield,
  Sparkles,
  Sun,
  Terminal,
  User as UserIcon,
} from 'lucide-react'
import { clearAllPresetSelectionStores } from '@/lib/stores/preset-selection'

export function AvatarPopover({ collapsed }: { collapsed?: boolean }) {
  const t = useTranslations('avatar')
  const tShell = useTranslations('shellLayout')
  const router = useRouter()
  const pathname = usePathname()
  const inAdminScope = pathname?.startsWith('/admin') ?? false
  const user = useAuthStore((s) => s.user)
  const { isAdmin } = useAdminAccess()
  const { theme, resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  const [langOpen, setLangOpen] = useState(false)
  const currentLocale = useLocale()

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true)
  }, [])

  const displayName = user?.display_name ?? null

  const onLogout = async () => {
    const client = createApiClient('')
    try {
      await logoutUser(client)
    } catch {
      /* ignore */
    }
    // Stop any live stream before clearing auth so a late terminalization
    // cannot buffer unread marks for the next login on this browser.
    useMessageStore.getState().clearStream()
    useMessageStore.getState().resetUnread()
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
    // eslint-disable-next-line react-hooks/immutability
    document.cookie = `NEXT_LOCALE=${lang}; path=/; SameSite=Lax`
    router.refresh()
  }

  return (
    <Popover>
      <PopoverTrigger
        aria-label={tShell('accountMenu')}
        className={
          collapsed
            ? 'flex items-center justify-center w-full rounded hover:bg-accent transition-colors duration-fast py-1'
            : 'w-full min-w-0 flex items-center gap-2 px-2 py-1.5 rounded hover:bg-accent transition-colors duration-fast group'
        }
      >
        <Avatar
          src={user?.avatar_url}
          seed={user?.avatar_seed ?? user?.id}
          name={displayName ?? user?.email}
          loading={user === null}
        />
        {!collapsed && (
          <span className="text-xs truncate flex-1 text-left text-foreground">
            {displayName ?? user?.email ?? '...'}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        className="w-56 p-1 shadow-lg border-border-strong"
      >
        <div className="px-2 py-2 border-b border-border mb-1">
          {displayName && (
            <div className="text-xs font-medium text-foreground truncate">{displayName}</div>
          )}
          <div className="text-2xs text-muted-foreground truncate">{user?.email}</div>
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

        <Link
          href="/settings/profile"
          className="flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
        >
          <UserIcon className="size-3.5 text-muted-foreground" />
          <span>{t('profileSettings')}</span>
        </Link>

        {mounted &&
          (() => {
            // Two orthogonal axes: flavor (default / operator) × mode (light / dark).
            // Compose to a concrete next-themes value: light, dark,
            // operator-light, operator-dark. resolvedTheme handles 'system'.
            const isOperator = theme === 'operator-light' || theme === 'operator-dark'
            const currentMode =
              theme === 'operator-light' || theme === 'light'
                ? 'light'
                : theme === 'operator-dark' || theme === 'dark'
                  ? 'dark'
                  : (resolvedTheme ?? 'light')
            const toggleMode = () => {
              const nextMode = currentMode === 'dark' ? 'light' : 'dark'
              setTheme(isOperator ? `operator-${nextMode}` : nextMode)
            }
            const toggleFlavor = () => {
              setTheme(isOperator ? currentMode : `operator-${currentMode}`)
            }
            return (
              <>
                <button
                  type="button"
                  onClick={toggleMode}
                  className="w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
                >
                  {currentMode === 'dark' ? (
                    <Sun className="size-3.5 text-muted-foreground" />
                  ) : (
                    <Moon className="size-3.5 text-muted-foreground" />
                  )}
                  <span>{currentMode === 'dark' ? t('lightTheme') : t('darkTheme')}</span>
                </button>
                <button
                  type="button"
                  onClick={toggleFlavor}
                  className="w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
                >
                  {isOperator ? (
                    <Sparkles className="size-3.5 text-muted-foreground" />
                  ) : (
                    <Terminal className="size-3.5 text-muted-foreground" />
                  )}
                  <span className="font-mono uppercase tracking-wider text-[11px]">
                    {isOperator ? 'Default theme' : 'Operator theme'}
                  </span>
                </button>
              </>
            )
          })()}

        {(() => {
          const languages = [
            { code: 'zh', label: '中文' },
            { code: 'en', label: 'English' },
          ] as const
          const currentLabel = languages.find((l) => l.code === currentLocale)?.label
          return (
            <div className="mt-1 pt-1 border-t border-border">
              <button
                type="button"
                onClick={() => setLangOpen((v) => !v)}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
              >
                <Languages className="size-3.5 text-muted-foreground shrink-0" />
                <span>{t('language')}</span>
                <span className="ml-auto text-[11px] text-muted-foreground">{currentLabel}</span>
                <ChevronDown
                  className={`size-3 text-muted-foreground transition-transform duration-fast ${langOpen ? '' : '-rotate-90'}`}
                />
              </button>
              {langOpen &&
                languages.map(({ code, label }) => (
                  <button
                    key={code}
                    type="button"
                    onClick={() => void onLanguageChange(code)}
                    className="w-full flex items-center gap-2 pl-8 pr-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
                  >
                    <span
                      className={
                        currentLocale === code
                          ? 'text-foreground font-medium'
                          : 'text-muted-foreground'
                      }
                    >
                      {label}
                    </span>
                    {currentLocale === code && (
                      <Check className="size-3.5 text-primary ml-auto shrink-0" />
                    )}
                  </button>
                ))}
            </div>
          )
        })()}

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
