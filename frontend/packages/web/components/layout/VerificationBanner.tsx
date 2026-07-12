'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import Link from 'next/link'
import { createApiClient, resendOtp, useAuthStore } from '@cubeplex/core'

export function VerificationBanner() {
  const t = useTranslations('verificationBanner')
  const user = useAuthStore((s) => s.user)
  const client = useMemo(() => createApiClient(''), [])
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    if (cooldown <= 0) return
    const id = setInterval(() => setCooldown((c) => c - 1), 1000)
    return () => clearInterval(id)
  }, [cooldown])

  const handleResend = useCallback(async () => {
    if (cooldown > 0 || !user) return
    try {
      await resendOtp(client, user.email)
      setCooldown(60)
    } catch {
      // Silently ignore; backend rate-limits on its side.
    }
  }, [client, user, cooldown])

  if (!user || user.is_verified) return null

  return (
    <div className="flex items-center justify-center gap-x-3 bg-warning/10 border-b border-warning/30 px-4 py-2 text-xs">
      <span className="text-warning-foreground">{t('message')}</span>
      {cooldown > 0 ? (
        <span className="text-muted-foreground">{t('resendIn', { seconds: cooldown })}</span>
      ) : (
        <button type="button" onClick={handleResend} className="text-primary underline">
          {t('resend')}
        </button>
      )}
      <Link
        href={`/verify-otp?email=${encodeURIComponent(user.email)}`}
        className="ml-1 rounded-md bg-primary px-2 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
      >
        {t('enterCode')}
      </Link>
    </div>
  )
}
