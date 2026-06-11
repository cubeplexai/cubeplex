'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { createApiClient, forgotPassword } from '@cubebox/core'

export default function ForgotPasswordPage() {
  const t = useTranslations('auth')
  const [email, setEmail] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    try {
      const client = createApiClient('')
      await forgotPassword(client, email)
    } catch {
      // Intentionally ignore — show success message regardless
    } finally {
      setSubmitting(false)
      setSubmitted(true)
    }
  }

  return (
    <div className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">{t('forgotPasswordTitle')}</h1>
      </div>
      {submitted ? (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground text-center">{t('forgotPasswordSent')}</p>
          <div className="text-center text-sm">
            <Link href="/login" className="underline">
              {t('backToSignIn')}
            </Link>
          </div>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block">
            <span className="text-sm text-foreground/80">{t('email')}</span>
            <input
              type="email"
              required
              autoComplete="email"
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {submitting ? t('sending') : t('sendResetLink')}
          </button>
          <div className="text-center text-sm text-foreground/60">
            <Link href="/login" className="underline">
              {t('backToSignIn')}
            </Link>
          </div>
        </form>
      )}
    </div>
  )
}
