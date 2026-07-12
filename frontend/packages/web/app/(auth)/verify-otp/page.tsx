'use client'

import { useCallback, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { createApiClient, resendOtp, useAuthStore, verifyOtp } from '@cubeplex/core'
import { OtpInput } from '@/components/auth/OtpInput'

const RESEND_COOLDOWN = 60

function VerifyOtpForm() {
  const t = useTranslations('auth')
  const router = useRouter()
  const params = useSearchParams()
  const email = params.get('email') ?? ''
  const next = params.get('next') ?? '/'
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    if (cooldown <= 0) return
    const id = setInterval(() => setCooldown((c) => Math.max(0, c - 1)), 1000)
    return () => clearInterval(id)
  }, [cooldown])

  const routeAfterVerification = useCallback(async () => {
    const client = createApiClient('')
    await useAuthStore.getState().loadMe(client)
    const me = useAuthStore.getState().user
    const safeNext = next.startsWith('/') && !next.startsWith('//') ? next : '/'
    if (
      me?.needs_onboarding &&
      !safeNext.startsWith('/orgs/invites/accept') &&
      !safeNext.startsWith('/invite')
    ) {
      router.push('/onboarding')
    } else {
      router.push(safeNext)
    }
  }, [next, router])

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (code.length !== 6 || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const client = createApiClient('')
      await verifyOtp(client, email, code)
      // verifyOtp sets the auth cookie on success (backend does this).
      // No loginUser call needed.
      await routeAfterVerification()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const onResend = async () => {
    if (cooldown > 0) return
    setError(null)
    try {
      await resendOtp(createApiClient(''), email)
      setCooldown(RESEND_COOLDOWN)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">{t('verifyOtpTitle')}</h1>
        <p className="text-sm text-foreground/60 mt-1">{t('verifyOtpSubtitle', { email })}</p>
      </div>
      <OtpInput value={code} onChange={setCode} disabled={submitting} />
      {error && <div className="text-sm text-destructive text-center">{error}</div>}
      <button
        type="submit"
        disabled={code.length !== 6 || submitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? t('verifying') : t('verify')}
      </button>
      <button
        type="button"
        onClick={onResend}
        disabled={cooldown > 0}
        className="w-full text-center text-xs text-muted-foreground underline disabled:opacity-50"
      >
        {cooldown > 0 ? t('resendIn', { seconds: cooldown }) : t('resendCode')}
      </button>
    </form>
  )
}

export default function VerifyOtpPage() {
  return <VerifyOtpForm />
}
