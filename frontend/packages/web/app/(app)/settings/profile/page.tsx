'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { ProfileForm } from '@/components/profile/ProfileForm'
import { ChangePasswordForm } from '@/components/profile/ChangePasswordForm'
import { ApiKeysSection } from '@/components/profile/ApiKeysSection'
import { DeleteAccountDialog } from '@/components/profile/DeleteAccountDialog'
import { DangerZone } from '@/components/management/DangerZone'
import { PageHeader } from '@/components/management/PageHeader'
import { Button } from '@/components/ui/button'

export default function ProfilePage() {
  const t = useTranslations('profile')
  const [deleteOpen, setDeleteOpen] = useState(false)

  return (
    <div className="flex h-full flex-col">
      <PageHeader title={t('title')} description={t('subtitle')} />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-6">
        <div className="mx-auto flex max-w-xl flex-col gap-8 pb-10">
          <ProfileForm />
          <hr className="border-border" />
          <ChangePasswordForm />
          <hr className="border-border" />
          <ApiKeysSection />
          <hr className="border-border" />
          <DangerZone title={t('dangerZone')}>
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-medium">{t('deleteAccountTitle')}</p>
                <p className="text-sm text-muted-foreground">{t('deleteAccountDesc')}</p>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0 border-danger-border text-danger-fg hover:bg-danger-surface"
                onClick={() => setDeleteOpen(true)}
              >
                {t('deleteAccountButton')}
              </Button>
            </div>
          </DangerZone>
          <DeleteAccountDialog open={deleteOpen} onOpenChange={setDeleteOpen} />
        </div>
      </div>
    </div>
  )
}
