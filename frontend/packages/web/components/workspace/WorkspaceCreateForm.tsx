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
    <form onSubmit={onSubmit} className="op-panel">
      <div className="op-panel__head">
        <h3>{t('panelTitle')}</h3>
      </div>
      <div className="op-panel__body space-y-3">
        <label className="block">
          <span className="block text-[12.5px] font-medium text-foreground mb-1.5">
            {t('nameLabel')}
          </span>
          <input
            type="text"
            required
            maxLength={64}
            className="block w-full rounded-md border border-border bg-card px-3 h-9 text-[13px] text-foreground placeholder:text-muted-foreground/60 outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 transition-shadow"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('namePlaceholder')}
          />
          <span className="block mt-1.5 text-[11.5px] text-muted-foreground">{t('nameHint')}</span>
        </label>
        {error && (
          <div className="text-[12.5px] text-destructive border border-destructive/30 bg-destructive/5 rounded-md px-3 py-2">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting || !name.trim()}
          className="inline-flex items-center justify-center gap-2 rounded-md bg-foreground text-background px-3 h-8 text-[12.5px] font-medium hover:bg-foreground/90 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          {submitting ? t('creating') : t('submit')}
        </button>
      </div>
    </form>
  )
}
