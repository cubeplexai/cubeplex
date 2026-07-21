'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'
import type { TraceSummary } from './types'

interface Props {
  traces: TraceSummary[]
  workspaceNames: Record<string, string>
  userNames: Record<string, string>
  conversationNames: Record<string, string>
}

// Renders the resolved name with the raw id as a tooltip; falls back to the
// raw id itself when the lookup has no entry (e.g. a deleted workspace/user,
// or the batch resolution for this id hasn't landed yet).
function EntityCell({
  id,
  names,
}: {
  id: string | null | undefined
  names: Record<string, string>
}) {
  if (!id) return <span>—</span>
  const name = names[id]
  return (
    <span title={id} className={name ? undefined : 'font-mono text-xs'}>
      {name ?? id}
    </span>
  )
}

// Matches the formatDuration convention used elsewhere in the app (e.g.
// components/chat/ToolCallItem.tsx): <60s -> "Ns", >=60s -> "Nm Ss" or "Nm".
// Extended with a sub-second case (that convention's callers only invoke it
// for durations already known to be >=1s) - rounding straight to seconds
// would show a confusing "0s" for the many sub-second trace runs here.
function formatDuration(ms: number): string {
  if (ms < 0) return '0s'
  if (ms < 1000) return `${ms}ms`
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s > 0 ? `${m}m${s}s` : `${m}m`
}

export function TraceListTable({ traces, workspaceNames, userNames, conversationNames }: Props) {
  const t = useTranslations('adminTraces.columns')
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs uppercase text-muted-foreground">
        <tr>
          <th className="px-3 py-2">{t('startTime')}</th>
          <th className="px-3 py-2">{t('duration')}</th>
          <th className="px-3 py-2">{t('model')}</th>
          <th className="px-3 py-2">{t('workspace')}</th>
          <th className="px-3 py-2">{t('user')}</th>
          <th className="px-3 py-2">{t('conversation')}</th>
          <th className="px-3 py-2">{t('spans')}</th>
        </tr>
      </thead>
      <tbody>
        {traces.map((tr) => (
          <tr key={tr.trace_id} className="border-t border-border/60 hover:bg-muted/40">
            <td className="px-3 py-2">
              <Link
                className="font-mono text-primary hover:underline"
                href={`/admin/traces/${encodeURIComponent(tr.trace_id)}`}
              >
                {new Date(tr.start_time).toLocaleString()}
              </Link>
            </td>
            <td className="px-3 py-2" title={`${tr.duration_ms} ms`}>
              {formatDuration(tr.duration_ms)}
            </td>
            <td className="px-3 py-2">{tr.model ?? '—'}</td>
            <td className="px-3 py-2">
              <EntityCell id={tr.workspace_id} names={workspaceNames} />
            </td>
            <td className="px-3 py-2">
              <EntityCell id={tr.user_id} names={userNames} />
            </td>
            <td className="px-3 py-2">
              <EntityCell id={tr.conversation_id} names={conversationNames} />
            </td>
            <td className="px-3 py-2">{tr.span_count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
