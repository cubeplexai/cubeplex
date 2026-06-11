'use client'

import { useTranslations } from 'next-intl'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface AdminTopBarProps {
  orgName: string
}

function handleBackToApp() {
  if (typeof window === 'undefined') return
  // Try to close popups opened by script; fall back to in-app navigation
  // when window.close() is silently denied (same-origin Link navigation
  // leaves window.opener populated, but the browser refuses close()).
  let closed = false
  if (window.opener) {
    try {
      window.close()
    } catch {
      /* ignored, falls through to nav */
    }
    closed = window.closed
  }
  if (!closed) window.location.href = '/'
}

export function AdminTopBar({ orgName }: AdminTopBarProps) {
  const t = useTranslations('admin')
  return (
    <header className="flex items-center gap-2 border-b border-border bg-card px-4 h-11 shrink-0">
      <div className="flex items-center gap-2">
        <div className="size-6 rounded bg-gradient-to-br from-card to-raised border border-border-strong grid place-items-center font-mono text-2xs text-muted-foreground">
          cx
        </div>
        <h1
          aria-label={t('title')}
          className="text-2xs uppercase tracking-wider font-medium text-faint m-0"
        >
          {t('title')}
        </h1>
        {orgName && (
          <>
            <span className="text-faint">/</span>
            <span className="text-sm font-medium text-foreground">{orgName}</span>
          </>
        )}
      </div>

      <div className="ml-auto flex items-center gap-2">
        <Button variant="outline" size="sm" className="gap-1.5" onClick={handleBackToApp}>
          <ArrowLeft className="size-3.5" />
          {t('backToApp')}
        </Button>
      </div>
    </header>
  )
}
