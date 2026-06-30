'use client'

import { use, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import {
  acceptOrgInvite,
  createApiClient,
  useAuthStore,
  type AcceptOrgInviteResult,
} from '@cubebox/core'

export default function AcceptOrgInvitePage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>
}) {
  const { token } = use(searchParams)
  const t = useTranslations('invite')
  const router = useRouter()
  const [result, setResult] = useState<AcceptOrgInviteResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    if (!token) {
      setError(t('invalidLink'))
      setLoading(false)
      return
    }
    const client = createApiClient('')
    acceptOrgInvite(client, token)
      .then(async (r) => {
        await useAuthStore.getState().loadMe(client)
        const me = useAuthStore.getState().user
        // Org membership established; if there's no workspace yet, the user
        // still needs the workspace-only onboarding wizard.
        if (me?.needs_onboarding) {
          router.replace('/onboarding')
          return
        }
        setResult(r)
        setLoading(false)
      })
      .catch((err: unknown) => {
        const status = (err as { status?: number })?.status
        if (status === 401) {
          const next = encodeURIComponent(`/orgs/invites/accept?token=${token}`)
          router.replace(`/login?next=${next}`)
          return
        }
        setError(t('expiredOrUsed'))
        setLoading(false)
      })
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [token, router, t])

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
        <h1 className="text-xl font-semibold">{t('joinedOrg')}</h1>
        <Link
          href="/"
          className="inline-block rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          {t('goHome')}
        </Link>
      </div>
    )
  }

  return null
}
