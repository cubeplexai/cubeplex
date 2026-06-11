'use client'

import { useTranslations } from 'next-intl'
import { ProfileForm } from '@/components/profile/ProfileForm'
import { ChangePasswordForm } from '@/components/profile/ChangePasswordForm'
import { PageHeader } from '@/components/management/PageHeader'

export default function ProfilePage() {
  const t = useTranslations('profile')
  return (
    <div className="flex h-full flex-col">
      <PageHeader title={t('title')} description={t('subtitle')} />
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-xl flex-col gap-8">
          <ProfileForm />
          <hr className="border-border" />
          <ChangePasswordForm />
        </div>
      </div>
    </div>
  )
}
