'use client'

import { useTranslations } from 'next-intl'
import type { Provider } from '@cubeplex/core'
import { Badge } from '@/components/ui/badge'
import { ProviderLogo } from './ProviderLogo'
import { cn } from '@/lib/utils'

interface ProviderCardProps {
  provider: Provider
  active: boolean
  onClick: () => void
}

export function ProviderCard({ provider, active, onClick }: ProviderCardProps) {
  const t = useTranslations('adminModels')
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`provider-card-${provider.name}`}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group/provider-card flex w-full flex-col gap-2 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex items-center gap-2.5">
        <ProviderLogo name={provider.name} logoUrl={provider.logo_url} logo={provider.logo} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-semibold">{provider.name}</span>
            {provider.is_system && (
              <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
                {t('systemBadge')}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {t('modelCount', { count: provider.model_count })}
          </p>
        </div>
      </div>
      <p className="truncate font-mono text-[10px] text-muted-foreground/80">{provider.base_url}</p>
    </button>
  )
}
