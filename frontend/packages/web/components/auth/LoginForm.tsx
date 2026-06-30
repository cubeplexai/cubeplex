'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { ApiError, createApiClient, loginUser, useAuthStore } from '@cubebox/core'
import { useDeploymentMode } from '@cubebox/core/hooks/useDeploymentMode'
import { GoogleLoginButton } from './GoogleLoginButton'
import { SSOButton } from './SSOButton'

interface SsoRequiredState {
  message: string
  loginUrl: string
}

interface EmailNotVerifiedState {
  message: string
}

/**
 * Extracts the SSO required signal from a 403 response. Backend returns:
 *   { "detail": { "code": "sso_required", "message": "...", "login_url": "..." } }
 * FastAPI's HTTPException wrapping keeps that dict under `err.detail`.
 */
function extractSsoRequired(err: unknown): SsoRequiredState | null {
  if (!(err instanceof ApiError) || err.status !== 403) return null
  if (err.code !== 'sso_required') return null
  const detail = err.detail
  if (!detail || typeof detail !== 'object') return null
  const loginUrl = (detail as Record<string, unknown>).login_url
  if (typeof loginUrl !== 'string' || !loginUrl) return null
  return { message: err.message, loginUrl }
}

function extractEmailNotVerified(err: unknown): EmailNotVerifiedState | null {
  if (!(err instanceof ApiError) || err.status !== 403) return null
  if (err.code !== 'email_not_verified') return null
  return { message: err.message }
}

export function LoginForm({ nextPath = '/' }: { nextPath?: string }) {
  const t = useTranslations('auth')
  const router = useRouter()
  const { mode } = useDeploymentMode()
  const singleTenant = mode === 'single_tenant'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [ssoRequired, setSsoRequired] = useState<SsoRequiredState | null>(null)
  const [emailNotVerified, setEmailNotVerified] = useState<EmailNotVerifiedState | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSsoRequired(null)
    setEmailNotVerified(null)
    setSubmitting(true)
    try {
      const client = createApiClient('')
      await loginUser(client, email, password)
      await useAuthStore.getState().loadMe(client)
      const safeNext = nextPath.startsWith('/') && !nextPath.startsWith('//') ? nextPath : '/'
      router.push(safeNext)
    } catch (err) {
      const ssoState = extractSsoRequired(err)
      if (ssoState) {
        setSsoRequired(ssoState)
        return
      }
      const emailState = extractEmailNotVerified(err)
      if (emailState) {
        setEmailNotVerified(emailState)
        return
      }
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
      <div className="text-right">
        <Link href="/forgot-password" className="text-xs text-muted-foreground underline">
          {t('forgotPassword')}
        </Link>
      </div>
      {ssoRequired ? (
        <div
          role="alert"
          className="space-y-2 rounded-md border border-border bg-muted/40 p-3 text-sm"
        >
          <div>{ssoRequired.message}</div>
          <Link
            href={ssoRequired.loginUrl}
            className="inline-flex w-full items-center justify-center rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
          >
            {t('continueToSSO')}
          </Link>
        </div>
      ) : emailNotVerified ? (
        <div
          role="alert"
          className="space-y-2 rounded-md border border-border bg-muted/40 p-3 text-sm"
        >
          <div>{emailNotVerified.message}</div>
          <Link
            href={`/verify-otp?email=${encodeURIComponent(email)}&next=${encodeURIComponent(nextPath)}`}
            className="inline-flex w-full items-center justify-center rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
          >
            {t('verifyEmailNow')}
          </Link>
        </div>
      ) : (
        error && <div className="text-sm text-destructive">{error}</div>
      )}
      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? t('signingIn') : t('signIn')}
      </button>
      <div className="relative my-2">
        <div className="absolute inset-0 flex items-center" aria-hidden="true">
          <span className="w-full border-t border-border" />
        </div>
        <div className="relative flex justify-center text-xs uppercase">
          <span className="bg-background px-2 text-muted-foreground">{t('or')}</span>
        </div>
      </div>
      <GoogleLoginButton />
      <SSOButton singleTenant={singleTenant} />
      <div className="text-center text-sm text-foreground/60">
        {t('newHere')}{' '}
        <Link href={`/register?next=${encodeURIComponent(nextPath)}`} className="underline">
          {t('createAccount')}
        </Link>
      </div>
    </form>
  )
}
