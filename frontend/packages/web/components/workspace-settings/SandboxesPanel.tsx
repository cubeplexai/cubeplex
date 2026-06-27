'use client'

import { Boxes, Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useMySandboxes } from '@/hooks/useMySandboxes'
import { EmptyState } from '@/components/shared/EmptyState'
import { SETTINGS_CONTENT_WIDTH, SectionHeader } from '@/components/shared/SectionHeader'
import { cn } from '@/lib/utils'
import { SandboxCard } from './sandboxes/SandboxCard'

interface SandboxesPanelProps {
  wsId: string
}

/**
 * Workspace settings → Sandboxes tab. Lists the caller's own sandbox
 * entities (any runtime status — a terminated sandbox still shows so the
 * user can restart or delete it). Each row offers Restart (stop container,
 * keep files) and Delete (soft-delete row + stop container). See spec §7.2.
 */
export function SandboxesPanel({ wsId }: SandboxesPanelProps): React.ReactElement {
  const t = useTranslations('wsSandboxes')
  const { data: sandboxes, mutate, isLoading } = useMySandboxes(wsId)

  return (
    <div className="flex h-full flex-col">
      <SectionHeader
        title={t('title')}
        description={t('subtitle')}
        contained={SETTINGS_CONTENT_WIDTH}
      />

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className={cn('flex w-full flex-col gap-6', SETTINGS_CONTENT_WIDTH)}>
          {isLoading ? (
            <div className="flex items-center justify-center py-16 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : !sandboxes?.length ? (
            <EmptyState icon={Boxes} title={t('empty')} description={t('emptyHint')} />
          ) : (
            <>
              <p className="text-sm text-muted-foreground">{t('intro')}</p>
              <ul className="divide-y divide-border rounded-lg border">
                {sandboxes.map((sb) => (
                  <SandboxCard
                    key={sb.id}
                    sandbox={sb}
                    wsId={wsId}
                    onMutated={() => void mutate()}
                  />
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
