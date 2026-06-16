'use client'

import { useTranslations } from 'next-intl'
import { Variable } from 'lucide-react'
import { type EnvEntryOut } from '@cubebox/core'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { TooltipProvider } from '@/components/ui/tooltip'
import { EmptyState } from '@/components/shared/EmptyState'
import { WarningCell } from './WarningCell'

export type TableMode = 'org' | 'workspace-admin' | 'workspace-member'

interface Props {
  mode: TableMode
  entries: EnvEntryOut[]
  loading: boolean
  error: string | null
  onEdit: (entry: EnvEntryOut) => void
  onDelete: (entry: EnvEntryOut) => void
}

function ScopeBadge({ scope }: { scope: 'workspace' | 'user' }) {
  if (scope === 'workspace') {
    return (
      <span className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-accent text-accent-foreground">
        ws
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-info-surface text-info-fg">
      me
    </span>
  )
}

export function EnvTable({ mode, entries, loading, error, onEdit, onDelete }: Props) {
  const t = useTranslations('wsSettings.sandboxEnv')
  const showScope = mode !== 'org'

  if (loading) {
    return (
      <div className="rounded-xl border border-border/70 bg-card/40 p-5 text-xs text-muted-foreground">
        {t('loading')}
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-xs text-destructive">
        {t('loadError', { error })}
      </div>
    )
  }

  if (entries.length === 0) {
    return <EmptyState icon={Variable} title={t('emptyTitle')} description={t('emptyHint')} />
  }

  return (
    <TooltipProvider>
      <div className="overflow-hidden rounded-xl border border-border/70">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border/70 bg-muted/40">
              <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
                {t('colName')}
              </th>
              {showScope && (
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
                  {t('colScope')}
                </th>
              )}
              <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
                {t('colType')}
              </th>
              <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
                {t('colHosts')}
              </th>
              <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
                {t('colWarnings')}
              </th>
              <th className="px-4 py-2.5 text-right font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
                {t('colActions')}
              </th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry, i) => (
              <tr
                key={entry.id}
                className={cn(
                  'border-b border-border/50 last:border-0',
                  i % 2 === 0 ? 'bg-background' : 'bg-muted/20',
                )}
              >
                <td className="px-4 py-2.5 font-mono text-xs">{entry.env_name}</td>
                {showScope && (
                  <td className="px-4 py-2.5">
                    <ScopeBadge scope={entry.scope as 'workspace' | 'user'} />
                  </td>
                )}
                <td className="px-4 py-2.5 text-muted-foreground">
                  {entry.is_secret ? t('typeSecret') : t('typeEnvValue')}
                </td>
                <td className="px-4 py-2.5 text-muted-foreground">
                  {entry.hosts && entry.hosts.length > 0 ? entry.hosts.join(', ') : '—'}
                </td>
                <td className="px-4 py-2.5">
                  <WarningCell warnings={entry.warnings} />
                </td>
                <td className="px-4 py-2.5 text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => onEdit(entry)}
                    >
                      {t('actionEdit')}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive"
                      onClick={() => onDelete(entry)}
                    >
                      {t('actionDelete')}
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </TooltipProvider>
  )
}
