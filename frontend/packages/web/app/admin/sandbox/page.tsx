'use client'

import { useTranslations } from 'next-intl'

import { ComingSoonCard } from '@/components/admin/ComingSoonCard'

export default function SandboxPage() {
  const t = useTranslations('adminSandbox')
  return (
    <ComingSoonCard
      title={t('title')}
      description={t('subtitle')}
      backlogRef="M2 完整版（v1 后续 spec）"
    />
  )
}
