'use client'

import { useEffect, useMemo, useState } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import Link from 'next/link'
import { createApiClient, confirmImLink } from '@cubebox/core'

type Status = 'verifying' | 'success' | 'error'

export function ImLinkPage() {
  const t = useTranslations('im.link')
  const searchParams = useSearchParams()
  const router = useRouter()
  const token = searchParams.get('token')
  const client = useMemo(() => createApiClient(''), [])

  const [status, setStatus] = useState<Status>('verifying')
  const [errorMsg, setErrorMsg] = useState('')
  const [platform, setPlatform] = useState('')

  useEffect(() => {
    if (!token) {
      setStatus('error')
      setErrorMsg(t('invalidToken'))
      return
    }
    confirmImLink(client, token)
      .then((result) => {
        setStatus('success')
        setPlatform(result.platform)
      })
      .catch((err) => {
        if (err?.status === 401) {
          const returnUrl = `/im-link?token=${encodeURIComponent(token)}`
          router.replace(`/login?redirect=${encodeURIComponent(returnUrl)}`)
          return
        }
        setStatus('error')
        setErrorMsg(err?.detail || t('error'))
      })
  }, [client, token, router, t])

  if (status === 'verifying') {
    return <p className="text-center text-sm text-muted-foreground">{t('verifying')}</p>
  }

  if (status === 'success') {
    return (
      <div className="text-center space-y-3">
        <p className="text-sm font-medium">{t('success', { platform })}</p>
        <Link href="/" className="text-sm text-primary underline">
          {t('goToApp')}
        </Link>
      </div>
    )
  }

  return (
    <div className="text-center space-y-3">
      <p className="text-sm text-destructive">{errorMsg}</p>
    </div>
  )
}
