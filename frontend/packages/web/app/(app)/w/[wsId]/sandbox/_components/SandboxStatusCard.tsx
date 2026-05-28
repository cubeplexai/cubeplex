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
    dot: 'bg-emerald-500',
    text: 'text-emerald-700 dark:text-emerald-400',
    pill: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/30 dark:bg-emerald-950/40 dark:text-emerald-300',
    label: 'Running',
  },
  provisioning: {
    dot: 'bg-amber-500',
    text: 'text-amber-700 dark:text-amber-400',
    pill: 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-400/30 dark:bg-amber-950/40 dark:text-amber-200',
    label: 'Provisioning',
  },
  paused: {
    dot: 'bg-sky-500',
    text: 'text-sky-700 dark:text-sky-400',
    pill: 'border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-400/30 dark:bg-sky-950/40 dark:text-sky-300',
    label: 'Paused',
  },
  terminated: {
    dot: 'bg-zinc-400',
    text: 'text-zinc-600 dark:text-zinc-400',
    pill: 'border-zinc-200 bg-zinc-50 text-zinc-700 dark:border-zinc-700/40 dark:bg-zinc-900/40 dark:text-zinc-300',
    label: 'Terminated',
  },
  absent: {
    dot: 'bg-zinc-300',
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
