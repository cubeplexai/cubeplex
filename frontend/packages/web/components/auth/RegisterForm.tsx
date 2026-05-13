'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { createApiClient, registerUser, loginUser, useAuthStore } from '@cubebox/core'

export function RegisterForm() {
  const t = useTranslations('auth')
  const router = useRouter()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const client = createApiClient('')
      const result = await registerUser(client, email, password)
      await loginUser(client, email, password)
      await useAuthStore.getState().loadMe(client)
      if (!result.default_workspace_id) {
        router.push('/setup')
      } else {
        router.push(`/w/${result.default_workspace_id}`)
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="op-panel">
      <div className="px-6 pt-6 pb-1">
        <p className="op-eyebrow mb-2">create account</p>
        <h1 className="text-[22px] font-semibold leading-tight text-foreground">
          {t('signUpTitle')}
        </h1>
      </div>
      <form onSubmit={onSubmit} className="px-6 pt-5 pb-5 space-y-3.5">
        <label className="block">
          <span className="block text-[12.5px] font-medium text-foreground mb-1.5">
            {t('email')}
          </span>
          <input
            type="email"
            required
            autoComplete="email"
            placeholder="you@company.com"
            className="block w-full rounded-md border border-border bg-card px-3 h-9 text-[13px] text-foreground placeholder:text-muted-foreground/60 outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 transition-shadow"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="block text-[12.5px] font-medium text-foreground mb-1.5">
            {t('password')}
          </span>
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            placeholder="at least 8 characters"
            className="block w-full rounded-md border border-border bg-card px-3 h-9 text-[13px] text-foreground placeholder:text-muted-foreground/60 font-mono outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 transition-shadow"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <span className="block mt-1.5 text-[11.5px] text-muted-foreground">
            8+ characters. We don&apos;t enforce complexity rules — pick something you can remember
            with a password manager.
          </span>
        </label>
        {error && (
          <div className="text-[12.5px] text-destructive border border-destructive/30 bg-destructive/5 rounded-md px-3 py-2">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="w-full inline-flex items-center justify-center gap-2 rounded-md bg-foreground text-background px-3 h-9 text-[13px] font-medium hover:bg-foreground/90 disabled:opacity-50 transition-colors"
        >
          {submitting ? t('creatingAccount') : t('signUp')}
        </button>
      </form>
      <div className="hairline-t px-6 py-3.5 text-[12.5px] text-muted-foreground bg-muted/40 flex items-center justify-between">
        <span>{t('alreadyHaveAccount')}</span>
        <Link
          href="/login"
          className="text-foreground font-medium hover:underline underline-offset-2"
        >
          {t('signIn')} →
        </Link>
      </div>
    </div>
  )
}
