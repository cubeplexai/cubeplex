'use client'

import { useEffect } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import Link from 'next/link'

export default function SSOCallbackPage() {
  const t = useTranslations('auth')
  const searchParams = useSearchParams()
  const router = useRouter()
  const error = searchParams.get('error')

  useEffect(() => {
    if (error) return
    // The backend OIDC/SAML callback endpoints do a 302 redirect, so this
    // page only renders if someone lands here directly. Fall back to home.
    router.push('/')
  }, [error, router])

  if (error) {
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-xl font-semibold">{t('ssoError')}</h1>
        <p className="text-sm text-destructive">{error}</p>
        <Link href="/login" className="text-sm text-primary underline">
          {t('backToLogin')}
        </Link>
      </div>
    )
  }

  return <p className="text-center text-sm text-muted-foreground">{t('loggingIn')}</p>
}
