// frontend/packages/web/app/(app)/w/[wsId]/sandbox-env/_components/EnvTable.tsx
'use client'

import { type EnvEntryOut } from '@cubebox/core'
import { cn } from '@/lib/utils'
import { WarningCell } from './WarningCell'

export type TableMode = 'org' | 'workspace-admin' | 'workspace-member'

interface Props {
  mode: TableMode
  entries: EnvEntryOut[]
  loading: boolean
  error: string | null
  onRotate: (entry: EnvEntryOut) => void
  onDelete: (entry: EnvEntryOut) => void
}

function ScopeBadge({ scope }: { scope: 'workspace' | 'user' }) {
  if (scope === 'workspace') {
    return (
      <span className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-violet-100 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">
        ws
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-sky-100 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
      me
    </span>
  )
}

export function EnvTable({ mode, entries, loading, error, onRotate, onDelete }: Props) {
  const showScope = mode !== 'org'

  if (loading) {
    return (
      <div className="rounded-xl border border-border/70 bg-card/40 p-5 text-xs text-muted-foreground">
        Loading…
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-xs text-destructive">
        Failed to load: {error}
      </div>
    )
  }

  if (entries.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border/60 bg-muted/20 p-6 text-center text-xs text-muted-foreground">
        No environment variables yet. Add a secret or plain value to inject it into your sandbox.
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-xl border border-border/70">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border/70 bg-muted/40">
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
              Name
            </th>
            {showScope && (
              <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
                Scope
              </th>
            )}
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
              Type
            </th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
              Hosts
            </th>
            <th className="px-4 py-2.5 text-left font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
              Warnings
            </th>
            <th className="px-4 py-2.5 text-right font-medium text-muted-foreground uppercase tracking-wide text-[10px]">
              Actions
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
                {entry.is_secret ? 'secret' : 'plain'}
              </td>
              <td className="px-4 py-2.5 text-muted-foreground">
                {entry.hosts && entry.hosts.length > 0 ? entry.hosts.join(', ') : '—'}
              </td>
              <td className="px-4 py-2.5">
                <WarningCell warnings={entry.warnings} />
              </td>
              <td className="px-4 py-2.5 text-right">
                <div className="flex items-center justify-end gap-3">
                  {entry.is_secret && (
                    <button
                      onClick={() => onRotate(entry)}
                      className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                      rotate
                    </button>
                  )}
                  <button
                    onClick={() => onDelete(entry)}
                    className="text-muted-foreground hover:text-destructive transition-colors"
                  >
                    delete
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
