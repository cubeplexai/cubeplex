'use client'

import { useTranslations } from 'next-intl'
import { OrgInfoCard } from '@/components/admin/settings/OrgInfoCard'
import { OrgLLMSettingsCard } from '@/components/admin/settings/OrgLLMSettingsCard'
import { PageHeader } from '@/components/management/PageHeader'

export default function SettingsPage() {
  const t = useTranslations('adminSettings')

  return (
    <div className="flex h-full flex-col">
      <PageHeader title={t('title')} description={t('subtitle')} />

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          <OrgInfoCard />
          <OrgLLMSettingsCard />
        </div>
      </div>
    </div>
  )
}
