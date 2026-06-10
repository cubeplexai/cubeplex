'use client'

import { useEffect, useState } from 'react'
import dynamic from 'next/dynamic'
import { Download } from 'lucide-react'
import type { AttachmentPanelInfo } from '@cubebox/core'
import { usePanelStore } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { ScrollArea } from '@/components/ui/scroll-area'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { PreviewLoading } from '@/components/panel/artifact/PreviewLoading'
import { PanelHeader } from '@/components/panel/PanelHeader'
import { getFileVisual } from '@/lib/fileIcons'
import { cn } from '@/lib/utils'

const PdfPreview = dynamic(
  () => import('@/components/panel/artifact/PdfPreview').then((m) => m.PdfPreview),
  {
    ssr: false,
    loading: () => <PreviewLoading />,
  },
)

const TEXT_MAX_BYTES = 5 * 1024 * 1024

interface Props {
  info: AttachmentPanelInfo
}

function humanSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function AttachmentPreviewView({ info }: Props): React.ReactElement {
  const t = useTranslations('panel.attachment')
  const close = usePanelStore((s) => s.close)
  const visual = getFileVisual({ filename: info.filename, mime_type: info.mimeType })

  return (
    <div className="flex h-full flex-col bg-background">
      <PanelHeader
        source={{
          kind: 'plain',
          icon: (
            <div className={cn('size-5 grid place-items-center rounded-xs shrink-0', visual.bg)}>
              <visual.Icon className={cn('size-3', visual.fg)} />
            </div>
          ),
          title: info.filename,
          subtitle: `${visual.label} · ${humanSize(info.sizeBytes)}`,
        }}
        actions={
          <a
            href={info.downloadUrl}
            download
            className="p-1 rounded-xs hover:bg-accent transition-colors duration-fast"
            aria-label={t('download')}
          >
            <Download className="size-3.5 text-muted-foreground" />
          </a>
        }
        onClose={close}
      />
      <Body info={info} family={visual.family} />
    </div>
  )
}

function Body({ info, family }: { info: AttachmentPanelInfo; family: string }): React.ReactElement {
  const t = useTranslations('panel.attachment')
  if (family === 'pdf') {
    return (
      <div className="flex-1 min-h-0">
        <PdfPreview fileUrl={info.downloadUrl} />
      </div>
    )
  }
  if (family === 'video') {
    return (
      <div className="flex-1 grid place-items-center bg-black">
        <video src={info.downloadUrl} controls className="max-h-full max-w-full" />
      </div>
    )
  }
  if (family === 'audio') {
    return (
      <div className="flex-1 grid place-items-center p-8">
        <audio src={info.downloadUrl} controls />
      </div>
    )
  }
  if (info.sizeBytes > TEXT_MAX_BYTES) {
    return (
      <div className="flex-1 grid place-items-center p-8 text-center text-sm text-muted-foreground">
        {t('tooLarge', { size: (info.sizeBytes / 1024 / 1024).toFixed(1) })}
      </div>
    )
  }
  return <TextBody info={info} family={family} />
}

function TextBody({
  info,
  family,
}: {
  info: AttachmentPanelInfo
  family: string
}): React.ReactElement {
  const t = useTranslations('panel.attachment')
  const [state, setState] = useState<
    { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; text: string }
  >({ kind: 'loading' })

  useEffect(() => {
    const ac = new AbortController()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState({ kind: 'loading' })
    void (async () => {
      try {
        const res = await fetch(info.downloadUrl, {
          credentials: 'include',
          signal: ac.signal,
        })
        if (!res.ok) {
          setState({ kind: 'error', message: `HTTP ${res.status}` })
          return
        }
        const text = await res.text()
        if (!ac.signal.aborted) setState({ kind: 'ready', text })
      } catch (err) {
        if (!ac.signal.aborted) {
          setState({ kind: 'error', message: (err as Error).message ?? 'Failed to load' })
        }
      }
    })()
    return () => ac.abort()
  }, [info.downloadUrl])

  if (state.kind === 'loading') {
    return (
      <div className="flex-1 grid place-items-center p-8 text-sm text-muted-foreground">
        {t('loading')}
      </div>
    )
  }
  if (state.kind === 'error') {
    return (
      <div className="flex-1 grid place-items-center p-8 text-sm text-destructive">
        {t('loadFailed', { message: state.message })}
      </div>
    )
  }
  return (
    <ScrollArea className="flex-1 p-4">
      {family === 'markdown' ? (
        <MarkdownWithCitations className="prose prose-sm dark:prose-invert max-w-none">
          {state.text}
        </MarkdownWithCitations>
      ) : family === 'csv' ? (
        <CsvTable text={state.text} />
      ) : family === 'json' ? (
        <pre className="font-mono text-sm whitespace-pre-wrap break-all">
          {prettyJson(state.text)}
        </pre>
      ) : (
        <pre className="font-mono text-sm whitespace-pre-wrap break-all">{state.text}</pre>
      )}
    </ScrollArea>
  )
}

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function CsvTable({ text }: { text: string }): React.ReactElement {
  const t = useTranslations('panel.attachment')
  const lines = text.split(/\r?\n/).filter(Boolean).slice(0, 1000)
  if (lines.length === 0)
    return <div className="p-4 text-sm text-muted-foreground">{t('empty')}</div>
  const rows = lines.map((line) => line.split(','))
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={i === 0 ? 'bg-muted font-medium' : ''}>
              {row.map((cell, j) => (
                <td key={j} className="border border-border px-2 py-1 align-top">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
