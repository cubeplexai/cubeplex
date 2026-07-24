'use client'

import { useMemo, useState } from 'react'
import dynamic from 'next/dynamic'
import useSWR from 'swr'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText, Files, GitCompare, History } from 'lucide-react'
import {
  createApiClient,
  deleteWorkspaceSkill,
  formatSkillLabel,
  installWorkspaceSkill,
  toggleWorkspaceSkill,
  type SkillContent,
  type SkillVersionDetail,
} from '@cubeplex/core'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { SkillCanonicalNameRow } from '@/components/skills/SkillNameHeading'
import { cn, proseClasses } from '@/lib/utils'
import type { WorkspaceSkillEntry, WorkspaceSkillState } from '@/hooks/useWorkspaceSkillsCatalog'

const ReactDiffViewer = dynamic(() => import('react-diff-viewer-continued'), { ssr: false })

interface WorkspaceSkillDetailProps {
  wsId: string
  skill: WorkspaceSkillEntry
  onActionDone: () => void
}

function stripFrontmatter(content: string): string {
  return content.replace(/^---\s*\n[\s\S]*?\n---\s*(\n|$)/, '')
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const STATE_KEY = {
  'org-enabled': 'stateEnabled',
  'org-disabled': 'stateDisabled',
  'workspace-private': 'statePrivate',
  available: 'stateAvailable',
} as const satisfies Record<WorkspaceSkillState, string>

const STATE_BADGE_VARIANT: Record<WorkspaceSkillState, { className: string }> = {
  'org-enabled': { className: 'border-success-border text-success-fg' },
  'org-disabled': { className: 'text-muted-foreground' },
  'workspace-private': { className: 'border-primary/40 text-primary' },
  available: { className: 'border-warning-border text-warning-fg' },
}

async function contentFetcher(url: string): Promise<SkillContent> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.json() as Promise<SkillContent>
}

async function versionsFetcher(url: string): Promise<SkillVersionDetail[]> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.json() as Promise<SkillVersionDetail[]>
}

async function textFetcher(url: string): Promise<string> {
  const res = await fetch(url, { credentials: 'include' })
  if (res.status === 422) return '__BINARY__'
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.text()
}

/** Encode each path segment while preserving "/" separators. */
function encodeFilePath(filePath: string): string {
  return filePath.split('/').map(encodeURIComponent).join('/')
}

// ─── Files Tab ───────────────────────────────────────────────────────────────

function FilesTab({
  wsId,
  skillId,
  version,
  files,
}: {
  wsId: string
  skillId: string
  version: string
  files: SkillContent['files']
}) {
  const t = useTranslations('wsSettings.skillDetail')
  const [selected, setSelected] = useState<string | null>(null)

  const fileUrl = selected
    ? `/api/v1/ws/${wsId}/skills/${skillId}/files/${encodeFilePath(selected)}?version=${encodeURIComponent(version)}`
    : null
  const { data: fileContent, isLoading: fileLoading } = useSWR<string>(fileUrl, textFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  if (files.length === 0)
    return <p className="text-xs text-muted-foreground">{t('noAdditionalFiles')}</p>

  return (
    <div className="grid min-h-[200px] grid-cols-[200px_1fr] gap-3 overflow-hidden rounded-lg border border-border/70">
      <ul className="flex flex-col divide-y divide-border/60 border-r border-border/60 overflow-y-auto">
        {files.map((f) => (
          <li key={f.rel_path}>
            <button
              type="button"
              onClick={() => setSelected(f.rel_path)}
              className={`w-full cursor-pointer px-3 py-2 text-left text-xs transition-colors hover:bg-accent/50 ${
                selected === f.rel_path ? 'bg-accent/70 font-medium' : ''
              }`}
            >
              <div className="truncate font-mono">{f.rel_path}</div>
              <div className="text-[10px] text-muted-foreground">{formatBytes(f.size)}</div>
            </button>
          </li>
        ))}
      </ul>
      <div className="overflow-auto p-3">
        {!selected && <p className="text-xs text-muted-foreground">{t('clickToPreview')}</p>}
        {selected && fileLoading && (
          <p className="text-xs text-muted-foreground">{t('previewLoading')}</p>
        )}
        {selected && fileContent === '__BINARY__' && (
          <p className="text-xs text-muted-foreground">{t('binaryFile')}</p>
        )}
        {selected && fileContent && fileContent !== '__BINARY__' && (
          <pre className="whitespace-pre-wrap break-all font-mono text-xs leading-relaxed text-foreground/80">
            {fileContent}
          </pre>
        )}
      </div>
    </div>
  )
}

// ─── Versions Tab ────────────────────────────────────────────────────────────

function VersionsTab({
  installedVersion,
  currentVersion,
  versions,
}: {
  installedVersion: string | null
  currentVersion: string
  versions: SkillVersionDetail[]
}) {
  const t = useTranslations('wsSettings.skillDetail')
  const sorted = versions.slice().sort((a, b) => b.version.localeCompare(a.version))

  if (sorted.length === 0) return <p className="text-xs text-muted-foreground">{t('noVersions')}</p>

  return (
    <ul className="flex flex-col gap-1.5">
      {sorted.map((v) => {
        const isInstalled = v.version === installedVersion
        const isCurrent = v.version === currentVersion
        return (
          <li
            key={v.id}
            className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2.5 text-xs"
          >
            <div className="flex items-center gap-2">
              <span className="font-mono font-semibold">v{v.version}</span>
              {isInstalled && (
                <Badge
                  variant="outline"
                  className="border-success-border text-[10px] text-success-fg"
                >
                  {t('versionInstalled')}
                </Badge>
              )}
              {isCurrent && !isInstalled && (
                <Badge variant="outline" className="text-[10px]">
                  {t('versionLatest')}
                </Badge>
              )}
            </div>
            <span className="text-muted-foreground">{new Date(v.created_at).toLocaleString()}</span>
          </li>
        )
      })}
    </ul>
  )
}

// ─── Compare Tab ─────────────────────────────────────────────────────────────

type FileStatus = 'added' | 'removed' | 'changed' | 'same'

interface FileDiffEntry {
  path: string
  status: FileStatus
}

const STATUS_STYLES: Record<FileStatus, { label: string; cls: string }> = {
  added: { label: 'added', cls: 'text-success-fg bg-success-solid/10' },
  removed: { label: 'removed', cls: 'text-danger-fg bg-danger-solid/10' },
  changed: { label: 'changed', cls: 'text-warning-fg bg-warning-solid/10' },
  same: { label: 'same', cls: 'text-muted-foreground bg-muted/40' },
}

function CompareTab({
  wsId,
  skillId,
  versions,
}: {
  wsId: string
  skillId: string
  versions: SkillVersionDetail[]
}) {
  const t = useTranslations('wsSettings.skillDetail')
  const sorted = versions.slice().sort((a, b) => b.version.localeCompare(a.version))

  const [vLeft, setVLeft] = useState<string>(sorted[1]?.version ?? '')
  const [vRight, setVRight] = useState<string>(sorted[0]?.version ?? '')
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [splitView, setSplitView] = useState(false)

  const leftKey = vLeft
    ? `/api/v1/ws/${wsId}/skills/${skillId}?version=${encodeURIComponent(vLeft)}`
    : null
  const rightKey = vRight
    ? `/api/v1/ws/${wsId}/skills/${skillId}?version=${encodeURIComponent(vRight)}`
    : null

  const { data: leftContent } = useSWR<SkillContent>(leftKey, contentFetcher, {
    revalidateOnFocus: false,
  })
  const { data: rightContent } = useSWR<SkillContent>(rightKey, contentFetcher, {
    revalidateOnFocus: false,
  })

  const fileDiffs = useMemo((): FileDiffEntry[] => {
    if (!leftContent || !rightContent) return []
    const leftMap = new Map(leftContent.files.map((f) => [f.rel_path, f]))
    const rightMap = new Map(rightContent.files.map((f) => [f.rel_path, f]))
    const allPaths = new Set([...leftMap.keys(), ...rightMap.keys()])
    const entries: FileDiffEntry[] = []
    for (const path of allPaths) {
      const a = leftMap.get(path)
      const b = rightMap.get(path)
      let status: FileStatus
      if (a === undefined) status = 'added'
      else if (b === undefined) status = 'removed'
      else if (a.content_hash !== b.content_hash) status = 'changed'
      else status = 'same'
      entries.push({ path, status })
    }
    return entries.sort((a, b) => {
      const order: FileStatus[] = ['changed', 'added', 'removed', 'same']
      return order.indexOf(a.status) - order.indexOf(b.status) || a.path.localeCompare(b.path)
    })
  }, [leftContent, rightContent])

  const fileLeftUrl =
    selectedFile && vLeft
      ? `/api/v1/ws/${wsId}/skills/${skillId}/files/${encodeFilePath(selectedFile)}?version=${encodeURIComponent(vLeft)}`
      : null
  const fileRightUrl =
    selectedFile && vRight
      ? `/api/v1/ws/${wsId}/skills/${skillId}/files/${encodeFilePath(selectedFile)}?version=${encodeURIComponent(vRight)}`
      : null
  const { data: fileLeft } = useSWR<string>(fileLeftUrl, textFetcher, { revalidateOnFocus: false })
  const { data: fileRight } = useSWR<string>(fileRightUrl, textFetcher, {
    revalidateOnFocus: false,
  })

  const ready = vLeft && vRight && vLeft !== vRight

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-end gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70">
            {t('versionA')}
          </label>
          <select
            value={vLeft}
            onChange={(e) => {
              setVLeft(e.target.value)
              setSelectedFile(null)
            }}
            className="rounded-md border border-border/70 bg-background px-2 py-1.5 text-xs"
          >
            {sorted.map((v) => (
              <option key={v.id} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
        </div>
        <span className="mb-2 text-muted-foreground">→</span>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70">
            {t('versionB')}
          </label>
          <select
            value={vRight}
            onChange={(e) => {
              setVRight(e.target.value)
              setSelectedFile(null)
            }}
            className="rounded-md border border-border/70 bg-background px-2 py-1.5 text-xs"
          >
            {sorted.map((v) => (
              <option key={v.id} value={v.version}>
                v{v.version}
              </option>
            ))}
          </select>
        </div>
        <div className="mb-1.5 ml-auto flex items-center gap-1 rounded-md border border-border/70 p-0.5">
          <button
            type="button"
            onClick={() => setSplitView(true)}
            className={`cursor-pointer rounded px-2 py-1 text-[11px] transition-colors ${splitView ? 'bg-accent font-medium' : 'text-muted-foreground hover:text-foreground'}`}
          >
            {t('compareSideBySide')}
          </button>
          <button
            type="button"
            onClick={() => setSplitView(false)}
            className={`cursor-pointer rounded px-2 py-1 text-[11px] transition-colors ${!splitView ? 'bg-accent font-medium' : 'text-muted-foreground hover:text-foreground'}`}
          >
            {t('compareInline')}
          </button>
        </div>
      </div>

      {!ready && <p className="text-xs text-muted-foreground">{t('selectTwoVersions')}</p>}

      {ready && (
        <div className="grid grid-cols-[180px_1fr] gap-3 overflow-hidden">
          <ul className="flex flex-col gap-1 overflow-y-auto">
            {fileDiffs.length === 0 && (
              <li className="text-xs text-muted-foreground">{t('loading')}</li>
            )}
            {fileDiffs.map(({ path, status }) => {
              const { label, cls } = STATUS_STYLES[status]
              return (
                <li key={path}>
                  <button
                    type="button"
                    onClick={() => setSelectedFile(path)}
                    className={`w-full cursor-pointer rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-accent/50 ${
                      selectedFile === path ? 'bg-accent/70' : ''
                    }`}
                  >
                    <div className="flex items-center gap-1.5">
                      <span
                        className={`shrink-0 rounded px-1 py-0.5 text-[9px] font-semibold uppercase ${cls}`}
                      >
                        {label}
                      </span>
                    </div>
                    <div className="mt-0.5 truncate font-mono text-[10px]">{path}</div>
                  </button>
                </li>
              )
            })}
          </ul>
          <div className="min-w-0 overflow-auto rounded-lg border border-border/70">
            {!selectedFile && (
              <div className="flex h-full items-center justify-center p-6 text-xs text-muted-foreground">
                {t('clickViewDiff')}
              </div>
            )}
            {selectedFile && (fileLeft === '__BINARY__' || fileRight === '__BINARY__') && (
              <div className="flex h-full items-center justify-center p-6 text-xs text-muted-foreground">
                {t('binaryDiff')}
              </div>
            )}
            {selectedFile && fileLeft !== '__BINARY__' && fileRight !== '__BINARY__' && (
              <ReactDiffViewer
                oldValue={fileLeft ?? ''}
                newValue={fileRight ?? ''}
                splitView={splitView}
                leftTitle={`v${vLeft} — ${selectedFile}`}
                rightTitle={`v${vRight} — ${selectedFile}`}
                useDarkTheme={false}
              />
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Workspace Actions ────────────────────────────────────────────────────────

function WorkspaceActions({
  wsId,
  skill,
  onDone,
}: {
  wsId: string
  skill: WorkspaceSkillEntry
  onDone: () => void
}) {
  const t = useTranslations('wsSettings.skillDetail')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function client() {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }

  async function run(action: () => Promise<unknown>): Promise<void> {
    setBusy(true)
    setError(null)
    try {
      await action()
      onDone()
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      {error && <span className="text-xs text-destructive">{error}</span>}
      {skill.workspaceState === 'org-enabled' && skill.installId && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => void run(() => toggleWorkspaceSkill(client(), skill.installId!, false))}
        >
          {busy ? t('actionDisabling') : t('actionDisable')}
        </Button>
      )}
      {skill.workspaceState === 'org-disabled' && skill.installId && (
        <Button
          size="sm"
          disabled={busy}
          onClick={() => void run(() => toggleWorkspaceSkill(client(), skill.installId!, true))}
        >
          {busy ? t('actionEnabling') : t('actionEnable')}
        </Button>
      )}
      {skill.workspaceState === 'workspace-private' && skill.installId && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => void run(() => deleteWorkspaceSkill(client(), skill.installId!))}
        >
          {busy ? t('actionRemoving') : t('actionRemove')}
        </Button>
      )}
      {skill.workspaceState === 'available' && (
        <Button
          size="sm"
          disabled={busy}
          onClick={() =>
            void run(() => installWorkspaceSkill(client(), skill.id, skill.current_version))
          }
        >
          {busy ? t('actionInstalling') : t('actionInstall')}
        </Button>
      )}
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function WorkspaceSkillDetail({ wsId, skill, onActionDone }: WorkspaceSkillDetailProps) {
  const t = useTranslations('wsSettings.skillDetail')
  const targetVersion = skill.installed_version ?? skill.current_version
  const contentKey = `/api/v1/ws/${wsId}/skills/${skill.id}?version=${encodeURIComponent(targetVersion)}`
  const versionsKey = `/api/v1/ws/${wsId}/skills/${skill.id}/versions`

  const { data: content, isLoading } = useSWR<SkillContent>(contentKey, contentFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
  const { data: versions } = useSWR<SkillVersionDetail[]>(versionsKey, versionsFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  const sortedVersions = (versions ?? []).slice().sort((a, b) => b.version.localeCompare(a.version))
  const hasMultipleVersions = sortedVersions.length > 1

  return (
    <div className="flex w-full flex-col gap-4 p-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">
            {formatSkillLabel(skill.name).primary}
          </h3>
          <Badge variant="outline" className="font-mono">
            v{targetVersion}
          </Badge>
          <Badge variant={skill.source === 'preinstalled' ? 'default' : 'secondary'}>
            {skill.source === 'preinstalled' ? t('sourcePreinstalled') : t('sourceUploaded')}
          </Badge>
          {skill.imported_from_registry_name && (
            <Badge variant="outline" className="gap-1 text-[11px]">
              <span className="text-muted-foreground">via</span>
              {skill.imported_from_registry_name}
            </Badge>
          )}
          <Badge
            variant="outline"
            className={cn(STATE_BADGE_VARIANT[skill.workspaceState].className)}
          >
            {t(STATE_KEY[skill.workspaceState])}
          </Badge>
          <div className="ml-auto">
            <WorkspaceActions wsId={wsId} skill={skill} onDone={onActionDone} />
          </div>
        </div>
        <SkillCanonicalNameRow
          name={skill.name}
          copyLabel={t('copyCanonical')}
          copiedLabel={t('copiedCanonical')}
        />
        {skill.description && (
          <p className="text-sm leading-relaxed text-muted-foreground">{skill.description}</p>
        )}
        {skill.keywords.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {skill.keywords.map((kw) => (
              <Badge key={kw} variant="outline" className="text-[11px]">
                {kw}
              </Badge>
            ))}
          </div>
        )}
      </header>

      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">
            <FileText className="size-3.5" />
            {t('overview')}
          </TabsTrigger>
          <TabsTrigger value="files">
            <Files className="size-3.5" />
            {content ? t('filesCount', { count: content.files.length }) : t('files')}
          </TabsTrigger>
          <TabsTrigger value="versions">
            <History className="size-3.5" />
            {sortedVersions.length > 0
              ? t('versionsCount', { count: sortedVersions.length })
              : t('versions')}
          </TabsTrigger>
          {hasMultipleVersions && (
            <TabsTrigger value="compare">
              <GitCompare className="size-3.5" />
              {t('compare')}
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          {isLoading && <p className="text-xs text-muted-foreground">{t('loadingContent')}</p>}
          {content && (
            <div className="rounded-lg border border-border/70 bg-card/40 px-4 py-3">
              <div className={proseClasses}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {stripFrontmatter(content.content)}
                </ReactMarkdown>
              </div>
            </div>
          )}
        </TabsContent>

        <TabsContent value="files" className="mt-4">
          {isLoading && <p className="text-xs text-muted-foreground">{t('previewLoading')}</p>}
          {content && (
            <FilesTab
              wsId={wsId}
              skillId={skill.id}
              version={targetVersion}
              files={content.files}
            />
          )}
        </TabsContent>

        <TabsContent value="versions" className="mt-4">
          <VersionsTab
            installedVersion={skill.installed_version ?? null}
            currentVersion={skill.current_version}
            versions={sortedVersions}
          />
        </TabsContent>

        {hasMultipleVersions && (
          <TabsContent value="compare" className="mt-4">
            <CompareTab wsId={wsId} skillId={skill.id} versions={sortedVersions} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
