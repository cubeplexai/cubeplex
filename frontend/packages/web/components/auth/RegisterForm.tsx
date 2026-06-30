'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { createApiClient, registerUser, loginUser, useAuthStore } from '@cubebox/core'
import { validatePassword } from '@cubebox/core/auth'
import { isInviteAcceptPath } from '@/lib/invitePath'

export function RegisterForm({ nextPath = '/' }: { nextPath?: string }) {
  const t = useTranslations('auth')
  const router = useRouter()
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      // Pre-validate password (UX only; backend is authoritative)
      const pwCheck = validatePassword(password, 'high')
      if (!pwCheck.ok) {
        setError(t('passwordTooWeak'))
        return
      }

      const client = createApiClient('')
      const result = await registerUser(client, email, password, displayName || undefined)
      const safeNext = nextPath.startsWith('/') && !nextPath.startsWith('//') ? nextPath : '/'

      if (result.verification_required) {
        router.push(
          `/verify-otp?email=${encodeURIComponent(email)}&next=${encodeURIComponent(safeNext)}`,
        )
        return
      }

      // Verification off: register set is_verified=true. Establish session + route.
      await loginUser(client, email, password)
      await useAuthStore.getState().loadMe(client)
      const me = useAuthStore.getState().user
      if (isInviteAcceptPath(safeNext)) {
        router.push(safeNext)
      } else if (me?.needs_onboarding) {
        router.push('/onboarding')
      } else {
        router.push(
          result.default_workspace_id ? `/w/${result.default_workspace_id}` : '/onboarding',
        )
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">{t('signUpTitle')}</h1>
      </div>
      <label className="block">
        <span className="text-sm text-foreground/80">{t('displayName')}</span>
        <input
          type="text"
          autoComplete="name"
          maxLength={100}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </label>
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
          minLength={8}
          autoComplete="new-password"
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
        {submitting ? t('creatingAccount') : t('signUp')}
      </button>
      <div className="text-center text-sm text-foreground/60">
        {t('alreadyHaveAccount')}{' '}
        <Link href={`/login?next=${encodeURIComponent(nextPath)}`} className="underline">
          {t('signIn')}
        </Link>
      </div>
    </form>
  )
}
