'use client'

import { use, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { createApiClient, acceptInvite, useAuthStore, type AcceptInviteResult } from '@cubebox/core'

export default function AcceptInvitePage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>
}) {
  const { token } = use(searchParams)
  const t = useTranslations('invite')
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const [result, setResult] = useState<AcceptInviteResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    if (!token) {
      setError(t('invalidLink'))
      setLoading(false)
      return
    }
    if (!user) {
      router.replace(`/login?next=${encodeURIComponent(`/invite/accept?token=${token}`)}`)
      return
    }
    const client = createApiClient('')
    acceptInvite(client, token)
      .then((r) => setResult(r))
      .catch(() => setError(t('expiredOrUsed')))
      .finally(() => setLoading(false))
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [token, user, router, t])

  if (loading) {
    return <p className="text-center text-sm text-muted-foreground">{t('accepting')}</p>
  }

  if (error) {
    return (
      <div className="space-y-4 text-center">
        <p className="text-sm text-destructive">{error}</p>
        <Link href="/" className="text-sm underline">
          {t('goHome')}
        </Link>
      </div>
    )
  }

  if (result) {
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-xl font-semibold">{t('joined')}</h1>
        <p className="text-sm text-muted-foreground">
          {t('joinedDescription', { workspace: result.workspace_name })}
        </p>
        <Link
          href={`/w/${result.workspace_id}`}
          className="inline-block rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          {t('openWorkspace')}
        </Link>
      </div>
    )
  }

  return null
}
