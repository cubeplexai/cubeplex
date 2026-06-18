'use client'

/**
 * Linked-identity list for a single SSO connection. Paginated via offset.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Trash2 } from 'lucide-react'
import {
  ApiError,
  createApiClient,
  listSsoIdentities,
  unlinkSsoIdentity,
  type ExternalIdentityResponse,
} from '@cubebox/core'

import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'

interface SSOIdentitiesListProps {
  ssoId: string
}

const PAGE_SIZE = 25

export function SSOIdentitiesList({ ssoId }: SSOIdentitiesListProps) {
  const t = useTranslations('adminAuthentication.identities')
  const client = useMemo(() => createApiClient(''), [])
  const [items, setItems] = useState<ExternalIdentityResponse[]>([])
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [confirmEid, setConfirmEid] = useState<string | null>(null)
  const [unlinking, setUnlinking] = useState(false)

  const load = useCallback(
    async (nextOffset: number, replace: boolean) => {
      setLoading(true)
      setError(null)
      try {
        const page = await listSsoIdentities(client, ssoId, {
          limit: PAGE_SIZE,
          offset: nextOffset,
        })
        setHasMore(page.length === PAGE_SIZE)
        setItems((prev) => (replace ? page : [...prev, ...page]))
        setOffset(nextOffset + page.length)
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : String(err)
        setError(msg)
      } finally {
        setLoading(false)
      }
    },
    [client, ssoId],
  )

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load-on-mount
    void load(0, true)
  }, [load])

  const confirmTarget = useMemo(
    () => items.find((it) => it.id === confirmEid) ?? null,
    [items, confirmEid],
  )

  const onUnlink = useCallback(async () => {
    if (!confirmTarget) return
    setUnlinking(true)
    try {
      await unlinkSsoIdentity(client, ssoId, confirmTarget.id)
      setItems((prev) => prev.filter((it) => it.id !== confirmTarget.id))
      setConfirmEid(null)
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err)
      toast.error(msg)
    } finally {
      setUnlinking(false)
    }
  }, [client, ssoId, confirmTarget])

  function formatDate(iso: string): string {
    try {
      return new Date(iso).toLocaleString()
    } catch {
      return iso
    }
  }

  return (
    <section
      className="rounded-xl border border-border/70 bg-card shadow-sm"
      data-testid="sso-identities"
    >
      <header className="border-b border-border px-5 py-3.5">
        <h2 className="text-sm font-medium">{t('title')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
      </header>

      {loading && items.length === 0 ? (
        <div className="px-5 py-10 text-center text-xs text-muted-foreground">{t('loading')}</div>
      ) : error ? (
        <div className="px-5 py-6 text-xs text-destructive">{error}</div>
      ) : items.length === 0 ? (
        <div className="px-5 py-10 text-center text-xs text-muted-foreground">{t('empty')}</div>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">{t('email')}</TableHead>
                <TableHead className="text-xs">{t('externalId')}</TableHead>
                <TableHead className="text-xs">{t('linkedAt')}</TableHead>
                <TableHead className="text-xs w-[80px]">{t('actions')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((it) => (
                <TableRow key={it.id}>
                  <TableCell>
                    <span className="text-sm">{it.external_email}</span>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {it.external_id}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatDate(it.created_at)}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 gap-1 text-xs text-destructive"
                      onClick={() => setConfirmEid(it.id)}
                      data-testid={`sso-unlink-${it.id}`}
                    >
                      <Trash2 className="size-3" />
                      {t('unlink')}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {hasMore && (
            <div className="flex justify-center border-t border-border px-5 py-3">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void load(offset, false)}
                disabled={loading}
              >
                {t('loadMore')}
              </Button>
            </div>
          )}
        </>
      )}

      <AlertDialog
        open={confirmEid !== null}
        onOpenChange={(v: boolean) => {
          if (!v) setConfirmEid(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('unlinkConfirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('unlinkConfirmBody', { email: confirmTarget?.external_email ?? '' })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={unlinking}>{t('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={unlinking}
              onClick={() => void onUnlink()}
              data-testid="sso-unlink-confirm"
            >
              {unlinking ? t('unlinking') : t('unlinkConfirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}
