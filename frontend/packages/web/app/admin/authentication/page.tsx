'use client'

/**
 * Admin > Authentication.
 *
 * Top-level state for the org's SSO connection. Renders one of:
 *   - Loading skeleton.
 *   - "Set up SSO" empty state if no connection exists.
 *   - Status panel + config form (+ identities list) once configured.
 *
 * Per CLAUDE.md scope-isolated pages: this is its own route. We do NOT
 * parameterize an existing settings page.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { ShieldCheck } from 'lucide-react'
import { ApiError, createApiClient, getOrgSso, type SsoConnectionResponse } from '@cubeplex/core'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/management/PageHeader'
import { SSOConfigForm } from '@/components/admin/SSOConfigForm'
import { SSOStatusPanel } from '@/components/admin/SSOStatusPanel'
import { SSOIdentitiesList } from '@/components/admin/SSOIdentitiesList'

interface OrgInfo {
  id: string
  name: string
  slug: string
}

export default function AdminAuthenticationPage() {
  const t = useTranslations('adminAuthentication')
  const client = useMemo(() => createApiClient(''), [])
  const [connection, setConnection] = useState<SsoConnectionResponse | null>(null)
  const [orgSlug, setOrgSlug] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [setupClicked, setSetupClicked] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function run() {
      setLoading(true)
      setError(null)
      try {
        const [conn, orgRes] = await Promise.all([
          getOrgSso(client),
          client.get('/api/v1/admin/org'),
        ])
        if (cancelled) return
        setConnection(conn)
        if (orgRes.ok) {
          const org = (await orgRes.json()) as OrgInfo
          setOrgSlug(org.slug)
        }
      } catch (err) {
        if (cancelled) return
        const msg = err instanceof ApiError ? err.message : String(err)
        setError(msg || t('loadFailed'))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [client, t])

  const onDeleted = useCallback(() => {
    setConnection(null)
    setSetupClicked(false)
  }, [])

  const showEmpty = !loading && connection === null && !setupClicked

  return (
    <div className="flex h-full flex-col">
      <PageHeader title={t('title')} description={t('subtitle')} />

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          {loading && (
            <div className="space-y-4" data-testid="sso-loading">
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-64 w-full" />
            </div>
          )}

          {!loading && error && (
            <div
              role="alert"
              className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive"
            >
              {error}
            </div>
          )}

          {showEmpty && <EmptyState onClick={() => setSetupClicked(true)} />}

          {!loading && (connection || setupClicked) && (
            <>
              {connection && (
                <SSOStatusPanel
                  connection={connection}
                  orgSlug={orgSlug}
                  onUpdated={setConnection}
                  onDeleted={onDeleted}
                />
              )}
              <SSOConfigForm connection={connection} orgSlug={orgSlug} onUpdated={setConnection} />
              {connection && <SSOIdentitiesList ssoId={connection.id} />}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function EmptyState({ onClick }: { onClick: () => void }) {
  const t = useTranslations('adminAuthentication.empty')
  return (
    <section
      className="rounded-xl border border-dashed border-border/70 bg-card/30 px-6 py-12 text-center"
      data-testid="sso-empty"
    >
      <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary">
        <ShieldCheck className="size-6" />
      </div>
      <h2 className="text-base font-medium">{t('title')}</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">{t('body')}</p>
      <Button className="mt-6" onClick={onClick} data-testid="sso-configure">
        {t('configure')}
      </Button>
    </section>
  )
}
