'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { createApiClient, toApiError } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { useAdminAccess } from '@/hooks/useAdminAccess'

export function OrgInfoCard() {
  const t = useTranslations('adminSettings.orgInfo')
  const { orgName } = useAdminAccess()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [origName, setOrigName] = useState('')
  const [origSlug, setOrigSlug] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const client = createApiClient('')
    client
      .get('/api/v1/admin/org')
      .then(async (res) => {
        if (res.ok) {
          const data = (await res.json()) as { id: string; name: string; slug: string }
          setName(data.name)
          setSlug(data.slug)
          setOrigName(data.name)
          setOrigSlug(data.slug)
        }
      })
      .catch(() => {
        setName(orgName)
        setOrigName(orgName)
      })
  }, [orgName])

  const dirty = name !== origName || slug !== origSlug

  const onSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const client = createApiClient('')
      const patch: Record<string, string> = {}
      if (name !== origName) patch.name = name
      if (slug !== origSlug) patch.slug = slug
      const res = await client.patch('/api/v1/admin/org', patch)
      if (!res.ok) {
        const err = await toApiError(res)
        throw err
      }
      const data = (await res.json()) as { id: string; name: string; slug: string }
      setOrigName(data.name)
      setOrigSlug(data.slug)
      toast.success(t('saved'))
    } catch (err) {
      const detail = (err as { detail?: string }).detail
      setError(detail === 'slug_taken' ? t('slugTaken') : t('saveError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="text-sm font-medium">{t('title')}</h2>
      </div>
      <div className="p-4 space-y-3">
        <label className="block">
          <span className="text-sm text-muted-foreground">{t('nameLabel')}</span>
          <input
            type="text"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="text-sm text-muted-foreground">{t('slugLabel')}</span>
          <input
            type="text"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono"
            value={slug}
            onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
          />
          <span className="text-xs text-muted-foreground">{t('slugHelp')}</span>
        </label>
        {error && <div className="text-sm text-destructive">{error}</div>}
        {dirty && (
          <Button onClick={onSave} disabled={saving}>
            {saving ? t('saving') : t('save')}
          </Button>
        )}
      </div>
    </section>
  )
}
