'use client'

import { useEffect } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import Link from 'next/link'

const ERROR_MESSAGES: Record<string, string> = {
  sso_state_expired: 'The login session has expired. Please try again.',
  sso_invalid_request: 'Invalid SSO request. Please try again.',
  sso_connection_inactive: 'The SSO connection is not active. Contact your administrator.',
  sso_idp_error: 'The identity provider returned an error. Contact your administrator.',
  sso_attribute_mapping_error:
    'The SSO connection is misconfigured (attribute mapping). Contact your administrator.',
  sso_provisioning_denied:
    'Auto-provisioning is disabled for this organization. Contact your administrator.',
  email_not_verified: 'Your email address is not verified with this identity provider.',
  user_inactive: 'Your account has been deactivated. Contact your administrator.',
  not_org_member: 'You are not a member of this organization.',
}

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
    const message = ERROR_MESSAGES[error] ?? error
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-xl font-semibold">{t('ssoError')}</h1>
        <p className="text-sm text-destructive">{message}</p>
        <Link href="/login" className="text-sm text-primary underline">
          {t('backToLogin')}
        </Link>
      </div>
    )
  }

  return <p className="text-center text-sm text-muted-foreground">{t('loggingIn')}</p>
}
