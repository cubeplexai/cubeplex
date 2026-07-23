'use client'

import { useState, useEffect, useRef, type Ref } from 'react'
import dynamic from 'next/dynamic'
import { useArtifactStore, usePanelStore, createApiClient } from '@cubeplex/core'
import type { Artifact, ArtifactVersion } from '@cubeplex/core'
import { Download, ChevronDown } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { PanelHeader } from '@/components/panel/PanelHeader'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { getArtifactIcon } from './artifactIcons'
import { PreviewLoading } from './PreviewLoading'
import { HtmlPreview } from './HtmlPreview'
import { ImagePreview } from './ImagePreview'
import { CodePreview } from './CodePreview'
import { DocumentPreview } from './DocumentPreview'
import { DataPreview } from './DataPreview'
import { FallbackPreview } from './FallbackPreview'
import { SkillArtifactPreview } from './SkillArtifactPreview'
import { OfficePreview } from './OfficePreview'
import { buildDownloadUrl, buildPreviewUrl } from './previewUtils'
import { ArtifactExpandDialog } from './ArtifactExpandDialog'

const PdfPreview = dynamic(() => import('./PdfPreview').then((m) => m.PdfPreview), {
  ssr: false,
  loading: () => <PreviewLoading />,
})

function isPdf(artifact: Artifact): boolean {
  if (artifact.mime_type === 'application/pdf') return true
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''
  return /\.pdf$/i.test(filename)
}

const OFFICE_EXTENSIONS = new Set(['.docx', '.xlsx', '.pptx'])

function isOfficeFile(artifact: Artifact): boolean {
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''
  const ext = filename.slice(filename.lastIndexOf('.')).toLowerCase()
  return OFFICE_EXTENSIONS.has(ext)
}

function useFormatRelativeTime(): (dateStr: string) => string {
  const t = useTranslations('panel.artifactPanel')
  return (dateStr: string): string => {
    const date = new Date(dateStr)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return t('justNow')
    if (diffMin < 60) return t('minutesAgo', { n: diffMin })
    const diffHr = Math.floor(diffMin / 60)
    if (diffHr < 24) return t('hoursAgo', { n: diffHr })
    const diffDay = Math.floor(diffHr / 24)
    return t('daysAgo', { n: diffDay })
  }
}

function VersionPopover({
  artifact,
  versions,
  selectedVersion,
  onSelectVersion,
  portalContainer,
}: {
  artifact: Artifact
  versions: ArtifactVersion[]
  selectedVersion: number | null
  onSelectVersion: (version: number | null) => void
  portalContainer: HTMLElement | null
}) {
  const formatRelativeTime = useFormatRelativeTime()
  const [open, setOpen] = useState(false)
  const currentVersion = selectedVersion ?? artifact.version

  if (artifact.version <= 1) {
    return null
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px]
          text-muted-foreground hover:bg-muted/80 transition-colors flex items-center gap-0.5"
      >
        v{currentVersion}
        <ChevronDown className="size-2.5" />
      </PopoverTrigger>
      <PopoverContent className="w-56 p-1" align="end" container={portalContainer}>
        <div className="max-h-48 overflow-y-auto">
          {versions.map((v) => (
            <button
              key={v.id}
              onClick={() => {
                onSelectVersion(v.version === artifact.version ? null : v.version)
                setOpen(false)
              }}
              className={`w-full text-left px-2 py-1.5 rounded text-xs flex items-center
                justify-between hover:bg-muted/50 transition-colors
                ${v.version === currentVersion ? 'bg-muted' : ''}`}
            >
              <span className="flex items-center gap-1.5">
                <span className="font-medium">v{v.version}</span>
                {v.name !== artifact.name && (
                  <span className="text-muted-foreground truncate max-w-[100px]">{v.name}</span>
                )}
              </span>
              <span className="text-muted-foreground text-[10px]">
                {formatRelativeTime(v.created_at)}
              </span>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}

function ArtifactPanelHeader({
  artifact,
  versions,
  selectedVersion,
  onSelectVersion,
  onClose,
  expand,
  expandButtonRef,
  workspaceId,
  portalContainer,
}: {
  artifact: Artifact
  versions: ArtifactVersion[]
  selectedVersion: number | null
  onSelectVersion: (version: number | null) => void
  onClose: () => void
  expand: { active: boolean; onToggle: () => void }
  expandButtonRef?: Ref<HTMLButtonElement>
  workspaceId: string
  portalContainer: HTMLElement | null
}) {
  const t = useTranslations('panel.artifactPanel')
  const Icon = getArtifactIcon(artifact)
  const downloadUrl = buildDownloadUrl(artifact, workspaceId, selectedVersion)

  return (
    <PanelHeader
      source={{
        kind: 'plain',
        /* eslint-disable-next-line react-hooks/static-components */
        icon: <Icon className="size-3.5 text-primary shrink-0" />,
        title: artifact.name,
      }}
      actions={
        <>
          <VersionPopover
            artifact={artifact}
            versions={versions}
            selectedVersion={selectedVersion}
            onSelectVersion={onSelectVersion}
            portalContainer={portalContainer}
          />
          <a
            href={downloadUrl}
            className="p-1 rounded-xs hover:bg-accent transition-colors duration-fast"
            title={t('download')}
          >
            <Download className="size-3.5 text-muted-foreground" />
          </a>
        </>
      }
      expand={expand}
      expandButtonRef={expandButtonRef}
      // Mobile sheet already fills the viewport — hide expand control below md.
      expandClassName="hidden md:inline-flex"
      onClose={onClose}
    />
  )
}

export function PreviewContent({
  artifact,
  version,
  workspaceId,
}: {
  artifact: Artifact
  version: number | null
  workspaceId: string
}) {
  if (isPdf(artifact)) {
    const filename = artifact.entry_file || artifact.path.split('/').pop() || 'file.pdf'
    const fileUrl = buildPreviewUrl(artifact, filename, version, workspaceId)
    return <PdfPreview fileUrl={fileUrl} />
  }

  if (isOfficeFile(artifact)) {
    return <OfficePreview artifact={artifact} version={version} workspaceId={workspaceId} />
  }

  switch (artifact.artifact_type) {
    case 'website':
      return <HtmlPreview artifact={artifact} version={version} workspaceId={workspaceId} />
    case 'image':
      return <ImagePreview artifact={artifact} version={version} workspaceId={workspaceId} />
    case 'code':
      return <CodePreview artifact={artifact} version={version} workspaceId={workspaceId} />
    case 'document':
      return <DocumentPreview artifact={artifact} version={version} workspaceId={workspaceId} />
    case 'data':
      return <DataPreview artifact={artifact} version={version} workspaceId={workspaceId} />
    case 'skill':
      return (
        <SkillArtifactPreview artifact={artifact} version={version} workspaceId={workspaceId} />
      )
    default:
      return <FallbackPreview artifact={artifact} version={version} workspaceId={workspaceId} />
  }
}

export function ArtifactPanel() {
  const view = usePanelStore((s) => s.view)
  const close = usePanelStore((s) => s.close)
  const artifacts = useArtifactStore((s) => s.artifacts)
  const versions = useArtifactStore((s) => s.versions)
  const selectedVersion = useArtifactStore((s) => s.selectedVersion)
  const loadVersions = useArtifactStore((s) => s.loadVersions)
  const selectVersion = useArtifactStore((s) => s.selectVersion)

  const artifactId = view.type === 'artifact' ? view.artifactId : null
  const conversationId = view.type === 'artifact' ? view.conversationId : null
  const artifact = conversationId && artifactId ? artifacts[conversationId]?.[artifactId] : null

  const { workspaceId } = useWorkspaceContext()
  const t = useTranslations('panel.artifactPanel')

  // Identity-keyed expand: when the selected artifact (or panel view) changes,
  // expanded becomes false without a post-paint effect — no stale theater flash.
  const identityKey = conversationId && artifactId ? `${conversationId}:${artifactId}` : null
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const expanded = expandedKey !== null && expandedKey === identityKey

  const openExpand = (): void => {
    if (identityKey) setExpandedKey(identityKey)
  }
  const closeExpand = (): void => {
    setExpandedKey(null)
  }

  const [railPortalEl, setRailPortalEl] = useState<HTMLElement | null>(null)
  const [theaterPortalEl, setTheaterPortalEl] = useState<HTMLElement | null>(null)
  // Focus exit-expand on open so Esc works before the user tabs into an iframe.
  const exitExpandButtonRef = useRef<HTMLButtonElement | null>(null)
  // Restore focus to rail Expand when the theater closes (if still mounted).
  const expandButtonRef = useRef<HTMLButtonElement | null>(null)
  // Expand is desktop-only; clear theater if the viewport drops below md so
  // finalFocus does not target a display:none control.
  const isDesktop = useMediaQuery('(min-width: 768px)', true)

  useEffect(() => {
    if (!isDesktop) setExpandedKey(null)
  }, [isDesktop])

  useEffect(() => {
    if (!artifact || artifact.version <= 1 || !conversationId || !artifactId) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    loadVersions(client, conversationId, artifactId)
  }, [artifact, conversationId, artifactId, loadVersions, workspaceId])

  if (view.type !== 'artifact' || !artifact || !workspaceId || !identityKey) return null

  const artifactVersions = versions[artifact.id] ?? []
  const currentSelectedVersion = selectedVersion[artifact.id] ?? null

  const handleSelectVersion = (v: number | null): void => {
    selectVersion(artifact.id, v)
  }

  const handlePanelClose = (): void => {
    closeExpand()
    close()
  }

  const headerProps = {
    artifact,
    versions: artifactVersions,
    selectedVersion: currentSelectedVersion,
    onSelectVersion: handleSelectVersion,
    workspaceId,
  }

  return (
    <div ref={setRailPortalEl} className="flex flex-col h-full bg-background">
      <ArtifactPanelHeader
        {...headerProps}
        onClose={handlePanelClose}
        expand={{
          active: expanded,
          onToggle: expanded ? closeExpand : openExpand,
        }}
        expandButtonRef={expandButtonRef}
        portalContainer={railPortalEl}
      />
      <div className="flex-1 overflow-hidden">
        {expanded ? (
          <div
            className="flex h-full items-center justify-center text-sm text-muted-foreground px-4
              text-center"
            data-testid="artifact-rail-placeholder"
          >
            {t('expandedPlaceholder')}
          </div>
        ) : (
          <div data-testid="artifact-rail-preview">
            <PreviewContent
              artifact={artifact}
              version={currentSelectedVersion}
              workspaceId={workspaceId}
            />
          </div>
        )}
      </div>

      {/* Mount theater only while expanded so PreviewContent has a single host.
          Modal backdrop makes the rail inert — exit expand, then rail Close. */}
      {expanded && (
        <ArtifactExpandDialog
          open
          onOpenChange={(open) => {
            if (!open) closeExpand()
          }}
          title={artifact.name}
          identityKey={identityKey}
          initialFocusRef={exitExpandButtonRef}
          finalFocusRef={expandButtonRef}
          header={
            <div ref={setTheaterPortalEl}>
              <ArtifactPanelHeader
                {...headerProps}
                onClose={closeExpand}
                expand={{ active: true, onToggle: closeExpand }}
                expandButtonRef={exitExpandButtonRef}
                portalContainer={theaterPortalEl}
              />
            </div>
          }
        >
          <PreviewContent
            artifact={artifact}
            version={currentSelectedVersion}
            workspaceId={workspaceId}
          />
        </ArtifactExpandDialog>
      )}
    </div>
  )
}
