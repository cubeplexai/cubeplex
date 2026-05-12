'use client'

import { useTranslations } from 'next-intl'

export default function InsightsPage() {
  const t = useTranslations('adminInsights')
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold">{t('heading')}</h1>
      <p className="text-sm text-muted-foreground mt-1">{t('loading')}</p>
    </div>
  )
}
