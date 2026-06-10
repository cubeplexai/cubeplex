'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { createApiClient, loginUser, useAuthStore } from '@cubebox/core'

export function LoginForm({ nextPath = '/' }: { nextPath?: string }) {
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
      await loginUser(client, email, password)
      await useAuthStore.getState().loadMe(client)
      const safeNext = nextPath.startsWith('/') && !nextPath.startsWith('//') ? nextPath : '/'
      router.push(safeNext)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">{t('signInTitle')}</h1>
      </div>
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
      <label className="block">
        <span className="text-sm text-foreground/80">{t('password')}</span>
        <input
          type="password"
          required
          autoComplete="current-password"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {error && <div className="text-sm text-destructive">{error}</div>}
      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? t('signingIn') : t('signIn')}
      </button>
      <div className="text-center text-sm text-foreground/60">
        {t('newHere')}{' '}
        <Link href="/register" className="underline">
          {t('createAccount')}
        </Link>
      </div>
    </form>
  )
}
