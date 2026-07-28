'use client'

import type { ReactNode } from 'react'
import { Lock } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useEdition } from '@cubeplex/core/hooks/useEdition'

/**
 * Wraps EE-only admin pages. The backend is authoritative (edition comes from
 * /system/info); this only keeps OSS users from hitting bare API errors when
 * they navigate straight to an EE route.
 */
export function EEGate({ children }: { children: ReactNode }) {
  const { edition, loading } = useEdition()
  const t = useTranslations('adminLayout')

  if (loading) return null
  if (edition === 'ee') return <>{children}</>

  return (
    <div className="max-w-2xl mx-auto mt-16 px-6">
      <div className="rounded-xl border border-dashed border-border bg-muted/20 px-6 py-10 text-center">
        <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Lock className="size-4" />
        </div>
        <p className="text-sm font-medium mb-1">{t('eeOnlyTitle')}</p>
        <p className="text-xs text-muted-foreground">{t('eeOnlyDescription')}</p>
      </div>
    </div>
  )
}
