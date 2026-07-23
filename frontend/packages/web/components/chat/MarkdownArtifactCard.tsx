'use client'

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import {
  ApiError,
  createApiClient,
  isMarkdownEditable,
  markdownFilename,
  updateArtifactContent,
  useArtifactStore,
  usePanelStore,
  type Artifact,
} from '@cubeplex/core'
import type { Editor } from '@tiptap/react'
import { useTranslations } from 'next-intl'
import { Download, Expand, FileText, Quote, Pencil, Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { buildDownloadUrl, buildPreviewUrl } from '@/components/panel/artifact/previewUtils'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { Button } from '@/components/ui/button'
import { useComposerDraft } from '@/hooks/useComposerDraft'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { cn, proseClasses } from '@/lib/utils'

const MarkdownRichEditor = dynamic(
  () => import('@/components/editor/MarkdownRichEditor').then((m) => m.MarkdownRichEditor),
  {
    ssr: false,
    loading: () => <div className="min-h-[12rem] animate-pulse rounded bg-muted/40" />,
  },
)

interface MarkdownArtifactCardProps {
  artifact: Artifact
}

function MarkdownArtifactCardImpl({ artifact: initial }: MarkdownArtifactCardProps) {
  const t = useTranslations('chatExtras')
  const { workspaceId } = useWorkspaceContext()
  const openPreview = usePanelStore((s) => s.openArtifact)
  const addOrUpdate = useArtifactStore((s) => s.addOrUpdate)
  const deleted = useArtifactStore((s) => s.isDeleted(initial.id))
  const setDraft = useComposerDraft((s) => s.setDraft)

  const live = useArtifactStore((s) => s.artifacts[initial.conversation_id]?.[initial.id])
  const artifact = live ?? initial

  const [mode, setMode] = useState<'read' | 'edit'>('read')
  const [content, setContent] = useState<string | null>(null)
  const [contentKey, setContentKey] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [selection, setSelection] = useState('')
  const bodyRef = useRef<HTMLDivElement>(null)
  const editorRef = useRef<Editor | null>(null)
  const editBaseline = useRef<string>('')

  const filename = markdownFilename(artifact) ?? 'document.md'
  const canEdit = isMarkdownEditable(artifact)
  const previewUrl = workspaceId
    ? buildPreviewUrl(artifact, filename, artifact.version, workspaceId)
    : null
  const downloadUrl = workspaceId ? buildDownloadUrl(artifact, workspaceId) : '#'

  // Keyed by preview URL so version bumps remount clean load state without
  // synchronous setState inside the effect body.
  const loadKey = previewUrl ?? ''
  useEffect(() => {
    if (!loadKey) return
    let cancelled = false
    fetch(loadKey)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status}`)
        return res.text()
      })
      .then((text) => {
        if (!cancelled) {
          setContent(text)
          setContentKey(loadKey)
          setLoadError(null)
        }
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setLoadError(e.message)
          setContent(null)
          setContentKey(loadKey)
        }
      })
    return () => {
      cancelled = true
    }
  }, [loadKey])

  const displayContent = contentKey === loadKey ? content : null
  const displayError = contentKey === loadKey ? loadError : null

  const handleOpenPanel = useCallback(() => {
    openPreview(artifact.conversation_id, artifact.id)
  }, [openPreview, artifact.conversation_id, artifact.id])

  const enterEdit = useCallback(() => {
    if (!canEdit || displayContent == null) return
    editBaseline.current = displayContent
    setDirty(false)
    setMode('edit')
  }, [canEdit, displayContent])

  const cancelEdit = useCallback(() => {
    setDirty(false)
    setMode('read')
    editorRef.current = null
  }, [])

  const handleEditorChange = useCallback((md: string) => {
    setDirty(md !== editBaseline.current)
  }, [])

  const handleSave = useCallback(async () => {
    if (!workspaceId || saving) return
    const client = createApiClient('')
    const md = editorRef.current != null ? editorRef.current.getMarkdown() : (displayContent ?? '')
    setSaving(true)
    try {
      const result = await updateArtifactContent(client, artifact.conversation_id, artifact.id, {
        content: md,
        expected_version: artifact.version,
      })
      addOrUpdate(artifact.conversation_id, result.artifact)
      setContent(md)
      if (workspaceId) {
        setContentKey(
          buildPreviewUrl(result.artifact, filename, result.artifact.version, workspaceId),
        )
      }
      setDirty(false)
      setMode('read')
      toast.success(t('mdSaved'))
      if (!result.sandbox_synced) {
        const reasonKey = result.sandbox_sync_reason
        const reasonMap: Record<string, string> = {
          no_sandbox: t('mdSandboxReason.no_sandbox'),
          no_path: t('mdSandboxReason.no_path'),
          path_is_directory: t('mdSandboxReason.path_is_directory'),
          path_missing: t('mdSandboxReason.path_missing'),
          path_escape: t('mdSandboxReason.path_escape'),
          sandbox_error: t('mdSandboxReason.sandbox_error'),
        }
        toast.message(t('mdSandboxPartial'), {
          description: reasonKey ? reasonMap[reasonKey] : undefined,
        })
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error(t('mdVersionConflict'))
      } else {
        toast.error(t('mdSaveFailed'))
      }
    } finally {
      setSaving(false)
    }
  }, [
    workspaceId,
    saving,
    displayContent,
    artifact.conversation_id,
    artifact.id,
    artifact.version,
    filename,
    addOrUpdate,
    t,
  ])

  const onSelectionChange = useCallback(() => {
    if (mode !== 'read') {
      setSelection('')
      return
    }
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || !bodyRef.current) {
      setSelection('')
      return
    }
    if (!bodyRef.current.contains(sel.anchorNode)) {
      setSelection('')
      return
    }
    const text = sel.toString().trim()
    setSelection(text)
  }, [mode])

  useEffect(() => {
    document.addEventListener('selectionchange', onSelectionChange)
    return () => document.removeEventListener('selectionchange', onSelectionChange)
  }, [onSelectionChange])

  const quoteSelection = useCallback(() => {
    if (!selection) return
    const quoted = selection
      .split('\n')
      .map((line) => `> ${line}`)
      .join('\n')
    const pathPart = artifact.path ? `, path: \`${artifact.path}\`` : ''
    const block =
      `${quoted}\n\n` +
      `Regarding artifact \`${artifact.id}\` (\`${artifact.name}\`, v${artifact.version}${pathPart}):\n`
    setDraft(block)
    setSelection('')
    toast.message(t('mdQuoted'))
  }, [selection, artifact, setDraft, t])

  const editorKey = useMemo(
    () => `${artifact.id}-v${artifact.version}-${mode}`,
    [artifact.id, artifact.version, mode],
  )

  if (deleted) return null

  return (
    <div className="my-2 overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <FileText className="size-4 shrink-0 text-primary" />
        <button
          type="button"
          className="min-w-0 flex-1 truncate text-left text-sm font-medium hover:underline"
          onClick={handleOpenPanel}
        >
          {artifact.name}
        </button>
        <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          v{artifact.version}
        </span>
        <a
          href={downloadUrl}
          download
          onClick={(e) => e.stopPropagation()}
          className="inline-flex size-7 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
          title={t('download')}
        >
          <Download className="size-3.5" />
        </a>
        <button
          type="button"
          onClick={handleOpenPanel}
          className="inline-flex size-7 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
          title={t('preview')}
        >
          <Expand className="size-3.5" />
        </button>
      </div>

      {mode === 'read' && (
        <>
          <div ref={bodyRef} className={cn('relative overflow-hidden', !expanded && 'max-h-80')}>
            {displayError && (
              <div className="p-4 text-sm text-destructive">
                {t('mdLoadFailed')}: {displayError}
              </div>
            )}
            {displayContent === null && !displayError && (
              <div className="p-4 text-sm text-muted-foreground">{t('mdLoading')}</div>
            )}
            {displayContent !== null && (
              <MarkdownWithCitations className={cn('p-4', proseClasses)}>
                {displayContent}
              </MarkdownWithCitations>
            )}
            {!expanded && displayContent && displayContent.length > 400 && (
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-card to-transparent" />
            )}
          </div>
          <div className="flex items-center gap-2 border-t border-border px-3 py-2">
            {displayContent && displayContent.length > 400 && (
              <Button
                type="button"
                variant="ghost"
                size="xs"
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? t('mdShowLess') : t('mdShowMore')}
              </Button>
            )}
            <div className="flex-1" />
            {selection && (
              <Button type="button" variant="outline" size="xs" onClick={quoteSelection}>
                <Quote className="size-3" />
                {t('mdQuote')}
              </Button>
            )}
            {canEdit && displayContent !== null && (
              <Button type="button" variant="secondary" size="xs" onClick={enterEdit}>
                <Pencil className="size-3" />
                {t('mdEdit')}
              </Button>
            )}
          </div>
        </>
      )}

      {mode === 'edit' && displayContent !== null && (
        <div className="p-2">
          <MarkdownRichEditor
            key={editorKey}
            initialMarkdown={displayContent}
            onChange={handleEditorChange}
            onSave={() => void handleSave()}
            onReady={(ed) => {
              editorRef.current = ed
              ed.commands.focus('end')
            }}
            placeholder={t('mdEditorPlaceholder')}
          />
          <div className="mt-2 flex items-center justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" disabled={saving} onClick={cancelEdit}>
              {t('mdCancel')}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={saving || !dirty}
              onClick={() => void handleSave()}
            >
              {saving ? <Loader2 className="size-3.5 animate-spin" /> : null}
              {saving ? t('mdSaving') : t('mdSave')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

export const MarkdownArtifactCard = memo(MarkdownArtifactCardImpl)
