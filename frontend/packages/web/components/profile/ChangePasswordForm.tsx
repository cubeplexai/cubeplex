'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { createApiClient, changePassword, ApiError } from '@cubebox/core'
import { Button } from '@/components/ui/button'

export function ChangePasswordForm() {
  const t = useTranslations('profile')
  const [current, setCurrent] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (newPw !== confirm) {
      setError(t('passwordMismatch'))
      return
    }
    setError(null)
    setSaving(true)
    try {
      const client = createApiClient('')
      await changePassword(client, current, newPw)
      toast.success(t('passwordChanged'))
      setCurrent('')
      setNewPw('')
      setConfirm('')
    } catch (err) {
      const isIncorrect =
        err instanceof ApiError &&
        (err.detail === 'incorrect_password' || err.code === 'incorrect_password')
      setError(isIncorrect ? t('incorrectPassword') : t('passwordChangeError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <h2 className="text-base font-medium mb-4">{t('changePassword')}</h2>
      <form onSubmit={onSubmit} className="space-y-3 max-w-sm">
        <input
          type="password"
          required
          placeholder={t('currentPassword')}
          autoComplete="current-password"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
        <input
          type="password"
          required
          minLength={8}
          placeholder={t('newPassword')}
          autoComplete="new-password"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={newPw}
          onChange={(e) => setNewPw(e.target.value)}
        />
        <input
          type="password"
          required
          minLength={8}
          placeholder={t('confirmPassword')}
          autoComplete="new-password"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        {error && <div className="text-sm text-destructive">{error}</div>}
        <Button type="submit" disabled={saving}>
          {saving ? t('saving') : t('updatePassword')}
        </Button>
      </form>
    </section>
  )
}
