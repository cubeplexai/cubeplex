'use client'

import { memo, useCallback, useEffect } from 'react'
import { Download, Package, Eye, PackagePlus, Loader2, Check, AlertCircle } from 'lucide-react'
import type { Artifact } from '@cubebox/core'
import { usePanelStore } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { getArtifactIcon, getArtifactLabel } from '@/components/panel/artifact/artifactIcons'
import { buildDownloadUrl } from '@/components/panel/artifact/previewUtils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { usePublishSkill } from '@/hooks/usePublishSkill'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { cn } from '@/lib/utils'

interface ArtifactCardProps {
  artifact: Artifact
}

function SkillInstallButton({
  workspaceId,
  artifactId,
  label,
}: {
  workspaceId: string
  artifactId: string
  label: string
}) {
  const t = useTranslations('chatExtras')
  const { publish, isPublishing, result, reset } = usePublishSkill(workspaceId, artifactId)

  // Auto-reset success state after 1.5s
  useEffect(() => {
    if (result?.ok) {
      const tm = setTimeout(reset, 1500)
      return () => clearTimeout(tm)
    }
  }, [result, reset])

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      void publish()
    },
    [publish],
  )

  if (isPublishing) {
    return (
      <button
        disabled
        className="flex size-8 items-center justify-center rounded-md text-muted-foreground"
      >
        <Loader2 className="size-4 animate-spin" />
      </button>
    )
  }

  if (result?.ok) {
    return (
      <button
        disabled
        className="flex size-8 items-center justify-center rounded-md text-green-600 dark:text-green-400"
      >
        <Check className="size-4" />
      </button>
    )
  }

  if (result && !result.ok) {
    const detail =
      result.message === 'VERSION_EXISTS' ? t('addToWorkspaceVersionExists') : result.message
    return (
      <Popover>
        <PopoverTrigger
          openOnHover
          delay={150}
          closeDelay={150}
          onClick={(e) => e.stopPropagation()}
          className="flex size-8 items-center justify-center rounded-md text-destructive
            transition-colors hover:bg-destructive/10"
        >
          <AlertCircle className="size-4" />
        </PopoverTrigger>
        <PopoverContent
          side="top"
          align="end"
          sideOffset={4}
          className="w-72 p-3"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex flex-col gap-2">
            <div className="text-sm font-medium text-destructive">{t('addToWorkspaceFailed')}</div>
            <p className="break-words text-xs text-muted-foreground">{detail}</p>
            <div className="flex justify-end">
              <button
                onClick={handleClick}
                className="rounded-md border border-border bg-background px-2.5 py-1
                  text-xs font-medium text-foreground transition-colors hover:bg-muted"
              >
                {t('retry')}
              </button>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    )
  }

  return (
    <button
      onClick={handleClick}
      title={label}
      className={cn(
        'flex size-8 items-center justify-center rounded-md',
        'text-muted-foreground transition-colors hover:bg-muted hover:text-foreground',
      )}
    >
      <PackagePlus className="size-4" />
    </button>
  )
}

export const ArtifactCard = memo(function ArtifactCard({ artifact }: ArtifactCardProps) {
  const t = useTranslations('chatExtras')
  const Icon = getArtifactIcon(artifact)
  const label = getArtifactLabel(artifact)
  const openPreview = usePanelStore((s) => s.openArtifact)
  const { workspaceId } = useWorkspaceContext()

  const downloadUrl = workspaceId ? buildDownloadUrl(artifact, workspaceId) : '#'

  const handlePreview = useCallback(() => {
    openPreview(artifact.conversation_id, artifact.id)
  }, [openPreview, artifact.conversation_id, artifact.id])

  return (
    <div
      className="my-2 rounded-lg border border-border bg-card p-3 cursor-pointer
        transition-colors hover:border-primary/30 hover:bg-card/80"
      onClick={handlePreview}
    >
      <div className="flex items-center gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10">
          <Icon className="size-4 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground">{artifact.name}</span>
            {artifact.version > 1 && (
              <span
                className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px]
                text-muted-foreground"
              >
                v{artifact.version}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Package className="size-3" />
            <span>{label}</span>
            {artifact.description && (
              <>
                <span className="text-muted-foreground/40">|</span>
                <span className="truncate">{artifact.description}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={(e) => {
              e.stopPropagation()
              handlePreview()
            }}
            className="flex size-8 items-center justify-center rounded-md
              text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title={t('preview')}
          >
            <Eye className="size-4" />
          </button>
          <a
            href={downloadUrl}
            onClick={(e) => e.stopPropagation()}
            className="flex size-8 items-center justify-center rounded-md
              text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title={t('download')}
          >
            <Download className="size-4" />
          </a>
          {artifact.artifact_type === 'skill' && workspaceId && (
            <SkillInstallButton
              workspaceId={workspaceId}
              artifactId={artifact.id}
              label={t('addToWorkspace')}
            />
          )}
        </div>
      </div>
    </div>
  )
})
