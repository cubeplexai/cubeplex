'use client'

import { useEffect, useRef, useState } from 'react'
import { Check, ChevronRight, Copy } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'

function CopyRequestButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      aria-label={label}
      title={label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
          if (timer.current) clearTimeout(timer.current)
          timer.current = setTimeout(() => setCopied(false), 2000)
        } catch {
          setCopied(false)
        }
      }}
    >
      {copied ? <Check className="text-success-fg" /> : <Copy />}
    </Button>
  )
}

export function CommandRequestView({ args }: { args: Record<string, unknown> }) {
  const t = useTranslations('panel.command')
  const command =
    typeof args.command === 'string' ? args.command : typeof args.cmd === 'string' ? args.cmd : null
  const options: [string, string][] = []
  if (typeof args.timeout_seconds === 'number') {
    options.push([t('timeout'), t('seconds', { count: args.timeout_seconds })])
  }
  if (typeof args.background === 'boolean') {
    options.push([t('execution'), args.background ? t('background') : t('foreground')])
  }
  if (typeof args.notify_on_complete === 'boolean') {
    options.push([t('notification'), args.notify_on_complete ? t('enabled') : t('disabled')])
  }
  if (typeof args.persistent === 'boolean') {
    options.push([t('persistent'), args.persistent ? t('enabled') : t('disabled')])
  }

  return (
    <div className="min-w-0 space-y-3">
      {typeof args.description === 'string' && args.description && (
        <p className="break-words text-sm text-foreground">{args.description}</p>
      )}
      {command !== null && (
        <section>
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted-foreground">{t('command')}</span>
            <CopyRequestButton text={command} label={t('copyCommand')} />
          </div>
          <pre
            className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded
              border border-border bg-sunken p-3 font-mono text-sm text-foreground"
          >
            {command}
          </pre>
        </section>
      )}
      {options.length > 0 && (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
          {options.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="break-words text-foreground">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <details className="group/request">
        <summary
          className="flex cursor-pointer list-none items-center gap-1 rounded text-xs
            text-muted-foreground hover:text-foreground focus-visible:ring-2
            focus-visible:ring-ring"
        >
          <ChevronRight aria-hidden className="size-3 group-open/request:rotate-90" />
          {t('rawRequest')}
        </summary>
        <div className="mt-2">
          <div className="mb-1 flex justify-end">
            <CopyRequestButton text={JSON.stringify(args, null, 2)} label={t('copyRequest')} />
          </div>
          <pre
            className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded
              bg-sunken p-3 font-mono text-xs text-foreground"
          >
            {JSON.stringify(args, null, 2)}
          </pre>
        </div>
      </details>
    </div>
  )
}
