'use client'

import { useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import useSWR from 'swr'
import type { Artifact } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { csrfHeaders, readApiError } from '@/lib/csrf'
import { cn, proseClasses } from '@/lib/utils'
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
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)

  const skillMdUrl = buildPreviewUrl(artifact, 'SKILL.md', version, workspaceId)
  const { data: skillMd, isLoading } = useSWR<string>(skillMdUrl, fetchText, {
    revalidateOnFocus: false,
  })

  async function handlePublish(): Promise<void> {
    setSubmitting(true)
    setResult(null)
    try {
      const res = await fetch(`/api/v1/ws/${workspaceId}/skills/publish`, {
        method: 'POST',
        credentials: 'include',
        headers: { ...csrfHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ artifact_id: artifact.id }),
      })
      if (res.status === 409) {
        setResult({ ok: false, message: '版本已存在，请在 SKILL.md 中更新 version 后再发布' })
        return
      }
      if (!res.ok) {
        setResult({ ok: false, message: await readApiError(res) })
        return
      }
      setResult({ ok: true, message: '已发布到组织市场' })
      setConfirmOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <header className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono font-semibold">{artifact.name}</span>
        <span className="text-xs text-muted-foreground">entry: SKILL.md</span>
        <span className="text-xs text-muted-foreground">v{artifact.version}</span>
      </header>

      {result && (
        <p
          className={cn(
            'rounded-md px-3 py-2 text-sm',
            result.ok
              ? 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300'
              : 'bg-destructive/10 text-destructive',
          )}
        >
          {result.message}
        </p>
      )}

      <div className={proseClasses}>
        {isLoading ? (
          <p className="text-sm text-muted-foreground">加载中…</p>
        ) : skillMd ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{skillMd}</ReactMarkdown>
        ) : (
          <p className="text-sm text-muted-foreground">未找到 SKILL.md</p>
        )}
      </div>

      <div className="border-t pt-3">
        <Button size="sm" onClick={() => setConfirmOpen(true)} disabled={!!result?.ok}>
          发布到组织市场
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
                确认发布
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
            <p className="mt-3 text-sm text-muted-foreground">
              将这个 skill 发布到组织市场。version 取自 SKILL.md
              frontmatter，发布后无法修改。若要更新，请在 SKILL.md 里 bump version 后重新发布。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmOpen(false)}
                disabled={submitting}
              >
                取消
              </Button>
              <Button size="sm" onClick={() => void handlePublish()} disabled={submitting}>
                {submitting ? '发布中…' : '确认发布'}
              </Button>
            </div>
          </DialogPrimitive.Popup>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </div>
  )
}
