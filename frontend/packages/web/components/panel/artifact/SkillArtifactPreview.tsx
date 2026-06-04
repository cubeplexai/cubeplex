'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X, AlertCircle, CheckCircle2 } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import useSWR from 'swr'
import type { Artifact } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { cn, proseClasses } from '@/lib/utils'
import { usePublishSkill } from '@/hooks/usePublishSkill'
import { buildPreviewUrl } from './previewUtils'

async function fetchText(url: string): Promise<string> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.text()
}

export function SkillArtifactPreview({
  artifact,
  version,
  workspaceId,
}: {
  artifact: Artifact
  version: number | null
  workspaceId: string
}) {
  const t = useTranslations('adminSkills')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const { publish, isPublishing, result } = usePublishSkill(workspaceId, artifact.id)

  const skillMdUrl = buildPreviewUrl(artifact, 'SKILL.md', version, workspaceId)
  const { data: skillMd, isLoading } = useSWR<string>(skillMdUrl, fetchText, {
    revalidateOnFocus: false,
  })

  async function handleConfirmPublish(): Promise<void> {
    await publish()
    setConfirmOpen(false)
  }

  const resultMessage =
    result?.message === 'VERSION_EXISTS'
      ? t('versionExists')
      : result?.message === 'SUCCESS'
        ? t('publishSuccess')
        : (result?.message ?? '')

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
        <header className="flex flex-wrap items-baseline gap-2">
          <span className="font-mono font-semibold">{artifact.name}</span>
          <span className="text-xs text-muted-foreground">entry: SKILL.md</span>
          <span className="text-xs text-muted-foreground">v{artifact.version}</span>
        </header>

        {result && (
          <div
            className={cn(
              'flex items-start gap-2 rounded-md border-l-4 px-3 py-2.5 text-sm font-medium',
              result.ok
                ? 'border-green-500 bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300'
                : 'border-destructive bg-destructive/10 text-destructive',
            )}
          >
            {result.ok ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
            ) : (
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
            )}
            <span>{resultMessage}</span>
          </div>
        )}

        <div className={proseClasses}>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">{t('previewLoading')}</p>
          ) : skillMd ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{skillMd}</ReactMarkdown>
          ) : (
            <p className="text-sm text-muted-foreground">{t('noSkillMd')}</p>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t p-4">
        <Button size="sm" onClick={() => setConfirmOpen(true)} disabled={!!result?.ok}>
          {t('publishButton')}
        </Button>
      </div>

      <DialogPrimitive.Root open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200" />
          <DialogPrimitive.Popup
            className={cn(
              'fixed left-1/2 top-1/2 z-50 w-[min(480px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2',
              'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
              'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200',
            )}
          >
            <div className="flex items-start justify-between gap-3">
              <DialogPrimitive.Title className="text-base font-semibold">
                {t('confirmPublishTitle')}
              </DialogPrimitive.Title>
              <DialogPrimitive.Close
                render={
                  <button
                    type="button"
                    aria-label="close"
                    className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <X className="size-4" />
                  </button>
                }
              />
            </div>
            <p className="mt-3 text-sm text-muted-foreground">{t('publishDesc')}</p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmOpen(false)}
                disabled={isPublishing}
              >
                {t('cancel')}
              </Button>
              <Button size="sm" onClick={() => void handleConfirmPublish()} disabled={isPublishing}>
                {isPublishing ? t('publishing') : t('confirmPublishBtn')}
              </Button>
            </div>
          </DialogPrimitive.Popup>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </div>
  )
}
