'use client'

import { use, useEffect, useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { ApiError, createApiClient, getOrgInfo, type OrgInfoResponse } from '@cubebox/core'
import { LoginForm } from '@/components/auth/LoginForm'
import { SSOButton } from '@/components/auth/SSOButton'

type FetchState =
  | { kind: 'loading' }
  | { kind: 'not_found' }
  | { kind: 'error'; message: string }
  | { kind: 'ok'; info: OrgInfoResponse }

export default function OrgLoginPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params)
  const t = useTranslations('auth')
  const [state, setState] = useState<FetchState>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const client = createApiClient('')
        const info = await getOrgInfo(client, slug)
        if (!cancelled) setState({ kind: 'ok', info })
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          setState({ kind: 'not_found' })
        } else {
          setState({ kind: 'error', message: (err as Error).message })
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [slug])

  if (state.kind === 'loading') {
    return (
      <div
        className="space-y-4 text-center text-sm text-foreground/60"
        aria-live="polite"
        aria-busy="true"
      >
        {t('loading')}
      </div>
    )
  }

  if (state.kind === 'not_found') {
    return (
      <div className="space-y-4 text-center" data-testid="org-not-found">
        <h1 className="text-xl font-semibold">{t('orgNotFound')}</h1>
        <p className="text-sm text-foreground/70">{t('orgNotFoundHint')}</p>
        <Link href="/login" className="inline-block text-sm underline">
          {t('backToGeneralLogin')}
        </Link>
      </div>
    )
  }

  // Transient backend error, or SSO disabled — fall back to the standard
  // password form so the page remains usable. Spec prefers fallback over
  // redirect so callers landing here can still complete login.
  if (state.kind === 'error' || !state.info.sso_enabled) {
    return <LoginForm nextPath="/" />
  }

  return (
    <div className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">
          {t('orgLoginTitle', { orgName: state.info.org_name })}
        </h1>
      </div>
      <SSOButton orgSlug={slug} />
      <div className="text-center text-sm text-foreground/60">
        {t('notAMember')}{' '}
        <Link href="/login" className="underline">
          {t('backToGeneralLogin')}
        </Link>
      </div>
    </div>
  )
}
