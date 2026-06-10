'use client'

import { useEffect, useMemo, useState } from 'react'
import { ExternalLink } from 'lucide-react'
import {
  createApiClient,
  getWorkspaceSandboxStatus,
  type SandboxStatusOut,
  type SandboxStatusValue,
} from '@cubebox/core'
import { cn } from '@/lib/utils'

interface Props {
  wsId: string
}

const STATUS_TONES: Record<
  SandboxStatusValue,
  { dot: string; text: string; pill: string; label: string }
> = {
  running: {
    dot: 'bg-success-solid',
    text: 'text-success-fg',
    pill: 'border-success-border bg-success-surface text-success-fg',
    label: 'Running',
  },
  provisioning: {
    dot: 'bg-warning-solid',
    text: 'text-warning-fg',
    pill: 'border-warning-border bg-warning-surface text-warning-fg',
    label: 'Provisioning',
  },
  paused: {
    dot: 'bg-info-solid',
    text: 'text-info-fg',
    pill: 'border-info-border bg-info-surface text-info-fg',
    label: 'Paused',
  },
  terminated: {
    dot: 'bg-faint',
    text: 'text-muted-foreground',
    pill: 'border-border bg-muted/40 text-muted-foreground',
    label: 'Terminated',
  },
  absent: {
    dot: 'bg-faint',
    text: 'text-muted-foreground',
    pill: 'border-border/70 bg-muted/40 text-muted-foreground',
    label: 'Not running',
  },
}

function formatRelative(iso: string | null): string {
  if (!iso) return '—'
  try {
    const dt = new Date(iso)
    return dt.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export function SandboxStatusCard({ wsId }: Props) {
  const client = useMemo(() => createApiClient(''), [])
  const [data, setData] = useState<SandboxStatusOut | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getWorkspaceSandboxStatus(client, wsId)
      .then((d) => !cancelled && setData(d))
      .catch((e: Error) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [client, wsId])

  if (error) {
    return (
      <section className="rounded-xl border border-destructive/40 bg-destructive/5 p-5 text-sm text-destructive shadow-sm">
        Failed to load sandbox status: {error}
      </section>
    )
  }
  if (!data) {
    return (
      <section className="rounded-xl border border-border/70 bg-card/40 p-5 text-xs text-muted-foreground shadow-sm">
        Loading…
      </section>
    )
  }

  const tone = STATUS_TONES[data.status]

  return (
    <section
      data-testid="sandbox-status-card"
      className="flex flex-col gap-5 rounded-xl border border-border/70 bg-card/50 p-6 shadow-sm"
    >
      <header className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <span
            className={cn(
              'inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium',
              tone.pill,
            )}
          >
            <span className={cn('size-1.5 rounded-full', tone.dot)} />
            {tone.label}
          </span>
          <h3 className="text-base font-semibold tracking-tight">Workspace sandbox</h3>
        </div>
        {data.browser_url && (
          <a
            href={data.browser_url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/70 bg-background px-3 text-xs font-medium shadow-sm transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <ExternalLink className="size-3.5" />
            Open browser
          </a>
        )}
      </header>

      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Image
          </dt>
          <dd className="font-mono text-xs text-foreground/90">{data.default_image ?? '—'}</dd>
        </div>
        <div className="flex flex-col gap-1">
          <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Last activity
          </dt>
          <dd className="text-xs text-foreground/90">{formatRelative(data.last_activity_at)}</dd>
        </div>
      </dl>

      {data.status === 'absent' && (
        <p className="rounded-md border border-dashed border-border/60 bg-muted/20 px-3 py-3 text-center text-xs text-muted-foreground">
          No sandbox running. One will start automatically the next time you send a message.
        </p>
      )}
    </section>
  )
}
