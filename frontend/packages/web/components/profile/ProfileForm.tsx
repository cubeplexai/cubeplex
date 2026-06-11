'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { createApiClient, updateProfile, useAuthStore } from '@cubebox/core'
import { Button } from '@/components/ui/button'

export function ProfileForm() {
  const t = useTranslations('profile')
  const user = useAuthStore((s) => s.user)
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [saving, setSaving] = useState(false)
  const dirty = displayName !== (user?.display_name ?? '')

  const onSave = async () => {
    setSaving(true)
    try {
      const client = createApiClient('')
      await updateProfile(client, { display_name: displayName })
      await useAuthStore.getState().loadMe(client)
      toast.success(t('saved'))
    } catch {
      toast.error(t('saveError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="space-y-4">
      <h2 className="text-base font-medium">{t('personalInfo')}</h2>
      <label className="block">
        <span className="text-sm text-muted-foreground">{t('displayNameLabel')}</span>
        <input
          type="text"
          maxLength={100}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-sm text-muted-foreground">{t('emailLabel')}</span>
        <input
          type="email"
          readOnly
          className="mt-1 w-full rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground"
          value={user?.email ?? ''}
        />
      </label>
      {dirty && (
        <Button onClick={onSave} disabled={saving}>
          {saving ? t('saving') : t('save')}
        </Button>
      )}
    </section>
  )
}
