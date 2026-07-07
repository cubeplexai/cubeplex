'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { ApiError, createApiClient, loginUser, useAuthStore } from '@cubebox/core'
import { useDeploymentMode } from '@cubebox/core/hooks/useDeploymentMode'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button, buttonVariants } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
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
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <h2 className="text-2xl font-semibold tracking-normal">{t('signInTitle')}</h2>
        <p className="text-sm leading-6 text-muted-foreground">{t('signInSubtitle')}</p>
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-2">
          <Label htmlFor="login-email">{t('email')}</Label>
          <Input
            id="login-email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="login-password">{t('password')}</Label>
          <Input
            id="login-password"
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
      </div>

      <div className="flex justify-end">
        <Link
          href="/forgot-password"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          {t('forgotPassword')}
        </Link>
      </div>

      {ssoRequired ? (
        <Alert>
          <AlertDescription className="flex flex-col gap-3">
            <span>{ssoRequired.message}</span>
            <Link href={ssoRequired.loginUrl} className={buttonVariants({ className: 'w-full' })}>
              {t('continueToSSO')}
            </Link>
          </AlertDescription>
        </Alert>
      ) : emailNotVerified ? (
        <Alert>
          <AlertDescription className="flex flex-col gap-3">
            <span>{emailNotVerified.message}</span>
            <Link
              href={`/verify-otp?email=${encodeURIComponent(email)}&next=${encodeURIComponent(nextPath)}`}
              className={buttonVariants({ className: 'w-full' })}
            >
              {t('verifyEmailNow')}
            </Link>
          </AlertDescription>
        </Alert>
      ) : (
        error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )
      )}

      <Button type="submit" disabled={submitting} size="lg" className="w-full">
        {submitting ? t('signingIn') : t('signIn')}
      </Button>

      <div className="flex items-center gap-3">
        <Separator className="flex-1" />
        <span className="text-xs text-muted-foreground">{t('or')}</span>
        <Separator className="flex-1" />
      </div>

      <div className="flex flex-col gap-2">
        <GoogleLoginButton />
        <SSOButton singleTenant={singleTenant} />
      </div>

      <div className="text-center text-sm text-muted-foreground">
        {t('newHere')}{' '}
        <Link
          href={`/register?next=${encodeURIComponent(nextPath)}`}
          className="font-medium text-foreground hover:text-primary"
        >
          {t('createAccount')}
        </Link>
      </div>
    </form>
  )
}
