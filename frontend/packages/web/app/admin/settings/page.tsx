'use client'

import { useTranslations } from 'next-intl'
import { OrgInfoCard } from '@/components/admin/settings/OrgInfoCard'
import { AdminPageShell } from '@/components/management/AdminPageShell'

export default function SettingsPage() {
  const t = useTranslations('adminSettings')

  return (
    <AdminPageShell title={t('title')} description={t('subtitle')}>
      <OrgInfoCard />
    </AdminPageShell>
  )
}
