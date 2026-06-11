'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient, requestVerifyToken, useAuthStore } from '@cubebox/core'

export function VerificationBanner() {
  const t = useTranslations('verificationBanner')
  const user = useAuthStore((s) => s.user)
  const client = useMemo(() => createApiClient(''), [])
  const [sent, setSent] = useState(false)

  if (!user || user.is_verified) return null

  const handleResend = async () => {
    await requestVerifyToken(client, user.email)
    setSent(true)
  }

  return (
    <div className="bg-warning/10 border-b border-warning/30 px-4 py-2 text-center text-xs">
      <span className="text-warning-foreground">{t('message')}</span>{' '}
      {sent ? (
        <span className="text-muted-foreground">{t('sent')}</span>
      ) : (
        <button
          type="button"
          onClick={() => void handleResend()}
          className="text-primary underline"
        >
          {t('resend')}
        </button>
      )}
    </div>
  )
}
