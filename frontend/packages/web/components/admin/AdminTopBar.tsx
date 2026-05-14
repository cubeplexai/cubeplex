'use client'

import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { AdminAvatarMenu } from './AdminAvatarMenu'

interface AdminTopBarProps {
  orgName: string
}

function handleBackToApp() {
  if (typeof window === 'undefined') return
  if (window.opener) {
    window.close()
  } else {
    window.location.href = '/'
  }
}

export function AdminTopBar({ orgName }: AdminTopBarProps) {
  const t = useTranslations('admin')
  const tLayout = useTranslations('adminLayout')
  return (
    <header className="flex items-center gap-3 border-b border-border bg-card px-4 h-12 shrink-0">
      <div className="flex items-center gap-2">
        <div
          className="w-[22px] h-[22px] rounded-[5px] bg-foreground text-background grid place-items-center font-mono font-semibold text-[11px] leading-none"
          aria-hidden
        >
          cb
        </div>
        <span className="font-display text-[13.5px] font-semibold tracking-tight text-foreground">
          cubebox
        </span>
      </div>
      <span className="h-4 w-px bg-border" aria-hidden />
      <div className="flex items-center gap-2">
        {/* h1 (not span) so e2e specs can find the section via getByRole('heading'). */}
        <h1 className="op-pill op-pill--accent !text-[10.5px] m-0">{t('title')}</h1>
        {orgName && (
          <span className="text-[12.5px] text-muted-foreground font-mono">
            {tLayout('orgPrefix', { name: orgName })}
          </span>
        )}
      </div>

      <div className="ml-auto flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={handleBackToApp} className="h-7 text-[12.5px]">
          {t('backToApp')} ↗
        </Button>
        <AdminAvatarMenu />
      </div>
    </header>
  )
}
