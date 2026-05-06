'use client'

import { useTranslations } from 'next-intl'
import { OrgLLMSettingsCard } from '@/components/admin/settings/OrgLLMSettingsCard'

export default function SettingsPage() {
  const t = useTranslations('adminSettings')

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          <OrgLLMSettingsCard />
        </div>
      </div>
    </div>
  )
}
