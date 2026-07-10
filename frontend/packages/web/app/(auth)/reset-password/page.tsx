'use client'

import { use, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { createApiClient, resetPassword } from '@cubebox/core'

export default function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>
}) {
  const { token } = use(searchParams)
  const t = useTranslations('auth')
  const router = useRouter()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (password !== confirm) {
      setError(t('passwordMismatch'))
      return
    }
    if (!token) {
      setError(t('invalidResetLink'))
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      const client = createApiClient('')
      await resetPassword(client, token, password)
      setSuccess(true)
      setTimeout(() => router.push('/login'), 3000)
    } catch {
      setError(t('invalidResetLink'))
    } finally {
      setSubmitting(false)
    }
  }

  if (success) {
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-xl font-semibold">{t('passwordResetSuccess')}</h1>
        <p className="text-sm text-muted-foreground">{t('redirectingToLogin')}</p>
      </div>
    )
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">{t('resetPasswordTitle')}</h1>
      </div>
      <label className="block">
        <span className="text-sm text-foreground/80">{t('newPassword')}</span>
        <input
          type="password"
          name="reset-password-new"
          required
          minLength={8}
          autoComplete="new-password"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-sm text-foreground/80">{t('confirmPassword')}</span>
        <input
          type="password"
          name="reset-password-confirm"
          required
          minLength={8}
          autoComplete="new-password"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </label>
      {error && <div className="text-sm text-destructive">{error}</div>}
      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? t('resetting') : t('resetPassword')}
      </button>
      <div className="text-center text-sm text-foreground/60">
        <Link href="/forgot-password" className="underline">
          {t('requestNewLink')}
        </Link>
      </div>
    </form>
  )
}
