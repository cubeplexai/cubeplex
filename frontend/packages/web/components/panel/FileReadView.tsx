'use client'

import { useEffect, useMemo, useRef } from 'react'
import { AlertTriangle, FileQuestion, Info } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { getFileVisual } from '@/lib/fileIcons'
import { cn } from '@/lib/utils'

interface Props {
  args: Record<string, unknown>
  result: string | null
  highlightText?: string | null
  highlightKey?: number
}

interface TextOutput {
  kind: 'text'
  path: string
  mime: string
  content: string
  size_bytes: number
  truncated?: boolean
  metadata?: Record<string, unknown>
}
interface NotebookCell {
  cell_type: 'code' | 'markdown' | 'raw'
  source: string
  outputs?: Array<Record<string, unknown>> | null
}
interface NotebookOutput {
  kind: 'notebook'
  path: string
  cells: NotebookCell[]
}
interface UnsupportedOutput {
  kind: 'unsupported'
  path: string
  mime: string
  size_bytes: number
  reason: string
  hint?: string
}
interface UnchangedOutput {
  kind: 'unchanged'
  path: string
}
interface ErrorOutput {
  kind: 'error'
  path: string
  error: string
  retryable?: boolean
}
type FileReadResult =
  | TextOutput
  | NotebookOutput
  | UnsupportedOutput
  | UnchangedOutput
  | ErrorOutput
  | { kind: string; path?: string }

function parseResult(raw: string | null): FileReadResult | null {
  if (!raw) return null
  try {
    return JSON.parse(raw) as FileReadResult
  } catch {
    return null
  }
}

function basename(path: string): string {
  const i = path.lastIndexOf('/')
  return i >= 0 ? path.slice(i + 1) : path
}

function humanSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function FileReadView({
  args,
  result,
  highlightText,
  highlightKey,
}: Props): React.ReactElement {
  const t = useTranslations('panel.fileRead')
  const parsed = useMemo(() => parseResult(result), [result])
  const path = parsed?.path ?? String(args.path ?? '')
  const visual = getFileVisual({ filename: basename(path) })

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <div className={cn('size-7 grid place-items-center rounded', visual.bg)}>
          <visual.Icon className={cn('size-3.5', visual.fg)} />
        </div>
        <div className="flex flex-col leading-tight min-w-0">
          <span className="truncate text-sm font-medium" title={path}>
            {basename(path) || t('untitled')}
          </span>
          <span className="text-[10px] text-muted-foreground truncate" title={path}>
            {path}
          </span>
        </div>
      </header>
      <MetaStrip parsed={parsed} args={args} />
      <Body parsed={parsed} highlightText={highlightText} highlightKey={highlightKey} />
    </div>
  )
}

function MetaStrip({
  parsed,
  args,
}: {
  parsed: FileReadResult | null
  args: Record<string, unknown>
}): React.ReactElement | null {
  const tr = useTranslations('panel.fileRead')
  if (!parsed) return null
  const range = (args.page_range || args.line_range) as string | undefined
  const chips: React.ReactNode[] = []
  if (parsed.kind === 'text') {
    const t = parsed as TextOutput
    chips.push(<Chip key="mime">{t.mime}</Chip>)
    chips.push(<Chip key="size">{humanSize(t.size_bytes)}</Chip>)
    chips.push(<Chip key="chars">{tr('chars', { count: t.content.length.toLocaleString() })}</Chip>)
    if (t.truncated) {
      chips.push(
        <Chip key="trunc" tone="warn">
          <AlertTriangle className="size-3" /> {tr('truncated')}
        </Chip>,
      )
    }
  } else if (parsed.kind === 'notebook') {
    const nb = parsed as NotebookOutput
    const code = nb.cells.filter((c) => c.cell_type === 'code').length
    const md = nb.cells.filter((c) => c.cell_type === 'markdown').length
    chips.push(<Chip key="cells">{tr('cells', { count: nb.cells.length })}</Chip>)
    chips.push(<Chip key="code">{tr('code', { count: code })}</Chip>)
    chips.push(<Chip key="md">{tr('md', { count: md })}</Chip>)
  } else if (parsed.kind === 'unsupported') {
    const u = parsed as UnsupportedOutput
    chips.push(<Chip key="mime">{u.mime}</Chip>)
    chips.push(<Chip key="size">{humanSize(u.size_bytes)}</Chip>)
  }
  if (range) chips.push(<Chip key="range">{tr('rangePrefix', { range })}</Chip>)
  if (!chips.length) return null
  return <div className="flex flex-wrap gap-1.5 border-b border-border px-4 py-2">{chips}</div>
}

function Chip({
  children,
  tone = 'normal',
}: {
  children: React.ReactNode
  tone?: 'normal' | 'warn'
}): React.ReactElement {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px]',
        tone === 'warn'
          ? 'border-warning-border bg-warning-solid/10 text-warning-fg'
          : 'border-border bg-muted text-muted-foreground',
      )}
    >
      {children}
    </span>
  )
}

function Body({
  parsed,
  highlightText,
  highlightKey,
}: {
  parsed: FileReadResult | null
  highlightText?: string | null
  highlightKey?: number
}): React.ReactElement {
  const t = useTranslations('panel.fileRead')
  const bodyRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!highlightText || !bodyRef.current) return
    const el = bodyRef.current
    const text = el.textContent ?? ''
    const search = highlightText.slice(0, 50)
    if (text.includes(search)) {
      el.classList.add('ring-2', 'ring-primary/50')
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
    return () => {
      el.classList.remove('ring-2', 'ring-primary/50')
    }
  }, [highlightText, highlightKey])

  if (!parsed) {
    return <div className="flex-1 p-4 text-sm text-muted-foreground">{t('noResult')}</div>
  }

  if (parsed.kind === 'text') {
    return (
      <div ref={bodyRef} className="flex-1 overflow-y-auto p-4">
        <MarkdownWithCitations className="prose prose-sm dark:prose-invert max-w-none">
          {(parsed as TextOutput).content}
        </MarkdownWithCitations>
      </div>
    )
  }
  if (parsed.kind === 'notebook') {
    const nb = parsed as NotebookOutput
    return (
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {nb.cells.map((cell, i) => (
          <div key={i} className="rounded-lg border border-border bg-card p-3">
            <div className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
              {cell.cell_type === 'code' ? `In [${i + 1}]` : cell.cell_type}
            </div>
            {cell.cell_type === 'markdown' ? (
              <MarkdownWithCitations className="prose prose-sm dark:prose-invert max-w-none">
                {cell.source}
              </MarkdownWithCitations>
            ) : (
              <pre className="font-mono text-xs whitespace-pre-wrap break-all">{cell.source}</pre>
            )}
            {cell.outputs && cell.outputs.length > 0 && (
              <div className="mt-2 border-t border-border pt-2">
                {cell.outputs.map((out, j) => (
                  <NotebookOutputBlock key={j} out={out} />
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    )
  }
  if (parsed.kind === 'unsupported') {
    const u = parsed as UnsupportedOutput
    return (
      <div className="flex-1 grid place-items-center p-8 text-center">
        <div className="space-y-3 max-w-sm">
          <FileQuestion className="mx-auto size-10 text-muted-foreground" />
          <h3 className="text-base font-medium">{t('unsupported')}</h3>
          <p className="text-sm text-muted-foreground">{u.reason}</p>
          {u.hint && (
            <div className="flex items-start gap-2 rounded-md border border-info-border bg-info-solid/10 px-3 py-2 text-left text-xs text-info-fg">
              <Info className="size-3.5 shrink-0 mt-0.5" />
              <span>{u.hint}</span>
            </div>
          )}
        </div>
      </div>
    )
  }
  if (parsed.kind === 'unchanged') {
    return (
      <div className="flex-1 grid place-items-center p-8 text-sm text-muted-foreground">
        {t('unchanged')}
      </div>
    )
  }
  if (parsed.kind === 'error') {
    const e = parsed as ErrorOutput
    return (
      <div className="flex-1 p-4">
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {e.error}
          {e.retryable ? ` ${t('retryable')}` : ''}
        </div>
      </div>
    )
  }
  return <div className="flex-1 p-4 text-sm text-muted-foreground">{t('unknown')}</div>
}

function NotebookOutputBlock({ out }: { out: Record<string, unknown> }): React.ReactElement {
  const text = (out.text ?? '') as string
  if (typeof text === 'string' && text) {
    return <pre className="font-mono text-xs whitespace-pre-wrap break-all">{text}</pre>
  }
  const data = out.data as Record<string, unknown> | undefined
  if (data?.['image/png']) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={`data:image/png;base64,${data['image/png']}`} alt="output" className="max-w-full" />
    )
  }
  return (
    <pre className="font-mono text-xs whitespace-pre-wrap break-all text-muted-foreground">
      {JSON.stringify(out, null, 2)}
    </pre>
  )
}
