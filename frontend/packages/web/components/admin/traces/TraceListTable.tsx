'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'
import type { TraceSummary } from './types'

interface Props {
  traces: TraceSummary[]
}

export function TraceListTable({ traces }: Props) {
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
            <td className="px-3 py-2">{tr.duration_ms} ms</td>
            <td className="px-3 py-2">{tr.model ?? '—'}</td>
            <td className="px-3 py-2 font-mono text-xs">{tr.workspace_id ?? '—'}</td>
            <td className="px-3 py-2 font-mono text-xs">{tr.user_id ?? '—'}</td>
            <td className="px-3 py-2 font-mono text-xs">{tr.conversation_id ?? '—'}</td>
            <td className="px-3 py-2">{tr.span_count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
