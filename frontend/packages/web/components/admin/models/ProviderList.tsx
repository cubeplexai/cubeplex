'use client'

import { useTranslations } from 'next-intl'
import type { Provider } from '@cubeplex/core'
import { ProviderCard } from './ProviderCard'

interface ProviderListProps {
  providers: Provider[]
  loading: boolean
  error: string | null
  selectedId: string | null
  onSelect: (id: string) => void
}

export function ProviderList({
  providers,
  loading,
  error,
  selectedId,
  onSelect,
}: ProviderListProps) {
  const t = useTranslations('adminModels')
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
        {t('loading')}
      </div>
    )
  }
  if (error) {
    return (
      <div className="m-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
        {t('loadFailed', { message: error })}
      </div>
    )
  }
  if (providers.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 px-6 text-center">
        <p className="text-sm text-muted-foreground">{t('noProviders')}</p>
        <p className="text-xs text-muted-foreground/70">{t('noProvidersHint')}</p>
      </div>
    )
  }
  return (
    <ul data-testid="providers-list" className="flex flex-col gap-1.5 p-3">
      {providers.map((p) => (
        <li key={p.id}>
          <ProviderCard provider={p} active={p.id === selectedId} onClick={() => onSelect(p.id)} />
        </li>
      ))}
    </ul>
  )
}
