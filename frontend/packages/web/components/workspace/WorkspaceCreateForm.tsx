'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, useWorkspaceStore } from '@cubebox/core'
import { useTranslations } from 'next-intl'

export function WorkspaceCreateForm() {
  const t = useTranslations('workspaceCreate')
  const router = useRouter()
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const client = createApiClient('')
      const ws = await useWorkspaceStore.getState().create(client, name)
      setName('')
      router.push(`/w/${ws.id}`)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3 rounded-md border border-border p-4">
      <label className="block">
        <span className="text-sm text-foreground/80">{t('nameLabel')}</span>
        <input
          type="text"
          required
          maxLength={64}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('namePlaceholder')}
        />
      </label>
      {error && <div className="text-sm text-danger-fg">{error}</div>}
      <button
        type="submit"
        disabled={submitting || !name.trim()}
        className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? t('creating') : t('submit')}
      </button>
    </form>
  )
}
