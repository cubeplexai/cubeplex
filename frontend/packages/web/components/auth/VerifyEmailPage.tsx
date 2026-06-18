'use client'

import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { useTranslations } from 'next-intl'
import Link from 'next/link'
import { createApiClient, verifyEmail, requestVerifyToken, useAuthStore } from '@cubebox/core'

export function VerifyEmailPage() {
  const t = useTranslations('verifyEmail')
  const searchParams = useSearchParams()
  const token = searchParams.get('token')
  const client = useMemo(() => createApiClient(''), [])
  const user = useAuthStore((s) => s.user)

  const [status, setStatus] = useState<'verifying' | 'success' | 'error'>('verifying')

  useEffect(() => {
    if (!token) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- mount-time validation
      setStatus('error')
      return
    }
    verifyEmail(client, token)
      .then(() => setStatus('success'))
      .catch(() => setStatus('error'))
  }, [client, token])

  const handleResend = async () => {
    if (!user?.email) return
    await requestVerifyToken(client, user.email)
  }

  if (status === 'verifying') {
    return <p className="text-center text-sm text-muted-foreground">{t('verifying')}</p>
  }

  if (status === 'success') {
    return (
      <div className="text-center space-y-3">
        <p className="text-sm font-medium">{t('success')}</p>
        <Link href="/" className="text-sm text-primary underline">
          {t('goToApp')}
        </Link>
      </div>
    )
  }

  return (
    <div className="text-center space-y-3">
      <p className="text-sm text-destructive">{t('error')}</p>
      {user?.email && (
        <button
          type="button"
          onClick={() => void handleResend()}
          className="text-sm text-primary underline"
        >
          {t('resend')}
        </button>
      )}
    </div>
  )
}
