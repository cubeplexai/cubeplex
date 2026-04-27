'use client'

import { useMemo, useState } from 'react'
import dynamic from 'next/dynamic'
import useSWR from 'swr'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText, Files, GitCompare, History, Network } from 'lucide-react'
import type { SkillContent, SkillVersionDetail } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { useAdminSkill } from '@/hooks/useAdminSkill'
import { proseClasses } from '@/lib/utils'
import { jsonHeaders, readApiError } from '@/lib/csrf'
import { OrgInstallActions } from './OrgInstallActions'
import { WorkspaceBindingsTable } from './WorkspaceBindingsTable'

// Dynamically import diff viewer to avoid SSR issues
const ReactDiffViewer = dynamic(() => import('react-diff-viewer-continued'), { ssr: false })

interface SkillDetailPanelProps {
  skillId: string | null
  onActionDone: () => void
}

const contentFetcher = async (url: string): Promise<SkillContent> => {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.json() as Promise<SkillContent>
}

const textFetcher = async (url: string): Promise<string> => {
  const res = await fetch(url, { credentials: 'include' })
  if (res.status === 422) return '__BINARY__'
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.text()
}

function stripFrontmatter(content: string): string {
  return content.replace(/^---\s*\n[\s\S]*?\n---\s*(\n|$)/, '')
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// ─── Files Tab ───────────────────────────────────────────────────────────────

function FilesTab({
  skillId,
  version,
  files,
}: {
  skillId: string
  version: string
  files: SkillContent['files']
}) {
  const [selected, setSelected] = useState<string | null>(null)

  const fileUrl = selected
    ? `/api/v1/admin/skills/${skillId}/versions/${version}/files/${selected}`
    : null
  const { data: fileContent, isLoading: fileLoading } = useSWR<string>(fileUrl, textFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  if (files.length === 0) return <p className="text-xs text-muted-foreground">该版本无附加文件。</p>

  return (
    <div className="grid min-h-[200px] grid-cols-[200px_1fr] gap-3 overflow-hidden rounded-lg border border-border/70">
      {/* File list */}
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

      {/* Preview pane */}
      <div className="overflow-auto p-3">
        {!selected && <p className="text-xs text-muted-foreground">点击左侧文件名预览内容。</p>}
        {selected && fileLoading && <p className="text-xs text-muted-foreground">加载中…</p>}
        {selected && fileContent === '__BINARY__' && (
          <p className="text-xs text-muted-foreground">无法预览：该文件为二进制文件。</p>
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
  skillId,
  skill,
  onInstalled,
}: {
  skillId: string
  skill: {
    install_state: string
    installed_version: string | null
    current_version: string
    versions: SkillVersionDetail[]
  }
  onInstalled: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const versions = skill.versions.slice().sort((a, b) => b.version.localeCompare(a.version))
  const installed = skill.install_state !== 'uninstalled'

  async function installVersion(version: string): Promise<void> {
    setBusy(version)
    setError(null)
    try {
      const res = await fetch(`/api/v1/admin/skills/${skillId}/install`, {
        method: 'POST',
        credentials: 'include',
        headers: jsonHeaders(),
        body: JSON.stringify({ version }),
      })
      if (!res.ok) throw new Error(await readApiError(res))
      onInstalled()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(null)
    }
  }

  if (versions.length === 0) return <p className="text-xs text-muted-foreground">暂无版本记录。</p>

  return (
    <div className="flex flex-col gap-2">
      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
          {error}
        </div>
      )}
      <ul className="flex flex-col gap-1.5">
        {versions.map((v) => {
          const isInstalled = v.version === skill.installed_version
          const isCurrent = v.version === skill.current_version
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
                    className="border-emerald-500/40 text-[10px] text-emerald-600"
                  >
                    已安装
                  </Badge>
                )}
                {isCurrent && !isInstalled && (
                  <Badge variant="outline" className="text-[10px]">
                    最新
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-3">
                <span className="text-muted-foreground">
                  {new Date(v.created_at).toLocaleString()}
                </span>
                {installed && !isInstalled && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 px-2 text-[11px]"
                    disabled={busy === v.version}
                    onClick={() => void installVersion(v.version)}
                  >
                    {busy === v.version ? '切换中…' : '安装此版本'}
                  </Button>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// ─── Compare Tab ─────────────────────────────────────────────────────────────

type FileStatus = 'added' | 'removed' | 'changed' | 'same'

interface FileDiffEntry {
  path: string
  status: FileStatus
  sizeA?: number
  sizeB?: number
}

const STATUS_STYLES: Record<FileStatus, { label: string; cls: string }> = {
  added: { label: 'added', cls: 'text-emerald-600 bg-emerald-500/10' },
  removed: { label: 'removed', cls: 'text-red-600 bg-red-500/10' },
  changed: { label: 'changed', cls: 'text-amber-600 bg-amber-500/10' },
  same: { label: 'same', cls: 'text-muted-foreground bg-muted/40' },
}

function CompareTab({ skillId, versions }: { skillId: string; versions: SkillVersionDetail[] }) {
  const sorted = versions.slice().sort((a, b) => b.version.localeCompare(a.version))

  const [vLeft, setVLeft] = useState<string>(sorted[1]?.version ?? '')
  const [vRight, setVRight] = useState<string>(sorted[0]?.version ?? '')
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [splitView, setSplitView] = useState(true)

  const leftKey = vLeft ? `/api/v1/admin/skills/${skillId}/versions/${vLeft}` : null
  const rightKey = vRight ? `/api/v1/admin/skills/${skillId}/versions/${vRight}` : null

  const { data: leftContent } = useSWR<SkillContent>(leftKey, contentFetcher, {
    revalidateOnFocus: false,
  })
  const { data: rightContent } = useSWR<SkillContent>(rightKey, contentFetcher, {
    revalidateOnFocus: false,
  })

  // Compute file diff list (compare by content hash; fall back to size)
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
      entries.push({ path, status, sizeA: a?.size, sizeB: b?.size })
    }
    return entries.sort((a, b) => {
      const order: FileStatus[] = ['changed', 'added', 'removed', 'same']
      return order.indexOf(a.status) - order.indexOf(b.status) || a.path.localeCompare(b.path)
    })
  }, [leftContent, rightContent])

  // Fetch selected file content from both sides
  const fileLeftUrl =
    selectedFile && vLeft
      ? `/api/v1/admin/skills/${skillId}/versions/${vLeft}/files/${selectedFile}`
      : null
  const fileRightUrl =
    selectedFile && vRight
      ? `/api/v1/admin/skills/${skillId}/versions/${vRight}/files/${selectedFile}`
      : null
  const { data: fileLeft } = useSWR<string>(fileLeftUrl, textFetcher, { revalidateOnFocus: false })
  const { data: fileRight } = useSWR<string>(fileRightUrl, textFetcher, {
    revalidateOnFocus: false,
  })

  const ready = vLeft && vRight && vLeft !== vRight

  return (
    <div className="flex flex-col gap-3">
      {/* Version selectors */}
      <div className="flex items-end gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70">
            版本 A（旧）
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
            版本 B（新）
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
            左右对比
          </button>
          <button
            type="button"
            onClick={() => setSplitView(false)}
            className={`cursor-pointer rounded px-2 py-1 text-[11px] transition-colors ${!splitView ? 'bg-accent font-medium' : 'text-muted-foreground hover:text-foreground'}`}
          >
            内联
          </button>
        </div>
      </div>

      {!ready && <p className="text-xs text-muted-foreground">请选择两个不同的版本进行对比。</p>}

      {ready && (
        <div className="grid grid-cols-[180px_1fr] gap-3 overflow-hidden">
          {/* File list */}
          <ul className="flex flex-col gap-1 overflow-y-auto">
            {fileDiffs.length === 0 && <li className="text-xs text-muted-foreground">加载中…</li>}
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

          {/* Diff pane */}
          <div className="min-w-0 overflow-auto rounded-lg border border-border/70">
            {!selectedFile && (
              <div className="flex h-full items-center justify-center p-6 text-xs text-muted-foreground">
                点击左侧文件查看 diff
              </div>
            )}
            {selectedFile && (fileLeft === '__BINARY__' || fileRight === '__BINARY__') && (
              <div className="flex h-full items-center justify-center p-6 text-xs text-muted-foreground">
                该文件为二进制文件，无法显示 diff。
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

// ─── Main Panel ──────────────────────────────────────────────────────────────

export function SkillDetailPanel({ skillId, onActionDone }: SkillDetailPanelProps) {
  const { skill, loading, error, refresh } = useAdminSkill(skillId)

  const contentKey =
    skill && skill.current_version
      ? `/api/v1/admin/skills/${skill.id}/versions/${skill.current_version}`
      : null
  const { data: content, isLoading: contentLoading } = useSWR<SkillContent>(
    contentKey,
    contentFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  if (!skillId) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
        选择一个 skill 查看详情
      </div>
    )
  }
  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
        加载中…
      </div>
    )
  }
  if (error || !skill) {
    return (
      <div className="m-6 flex-1 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
        加载失败：{error?.message ?? 'skill 不存在'}
      </div>
    )
  }

  const installed = skill.install_state !== 'uninstalled'
  const versions = skill.versions.slice().sort((a, b) => b.version.localeCompare(a.version))

  const handleActionDone = () => {
    void refresh()
    onActionDone()
  }

  return (
    <div className="flex w-full flex-col gap-4 p-6" data-testid="skill-detail-panel">
      {/* Header */}
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">{skill.name}</h3>
          <Badge variant="outline" className="font-mono">
            v{skill.current_version}
          </Badge>
          <Badge variant={skill.source === 'preinstalled' ? 'default' : 'secondary'}>
            {skill.source === 'preinstalled' ? '内置' : '组织上传'}
          </Badge>
          {skill.install_state === 'installed' && (
            <Badge variant="outline" className="border-emerald-500/40 text-emerald-600">
              已安装
            </Badge>
          )}
          {skill.install_state === 'update_available' && (
            <Badge variant="outline" className="border-amber-500/40 text-amber-600">
              可升级
            </Badge>
          )}
        </div>
        {skill.description && (
          <p className="text-sm leading-relaxed text-muted-foreground">{skill.description}</p>
        )}
        <div className="flex flex-wrap gap-1">
          {skill.keywords.map((kw) => (
            <Badge key={kw} variant="outline" className="text-[11px]">
              {kw}
            </Badge>
          ))}
        </div>
      </header>

      {/* Install actions */}
      <section>
        <h4 className="mb-2 text-sm font-medium uppercase tracking-wide text-muted-foreground/80">
          组织级安装
        </h4>
        <OrgInstallActions key={skill.id} skill={skill} onActionDone={handleActionDone} />
      </section>

      {/* Tabs */}
      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">
            <FileText className="size-3.5" />
            概览
          </TabsTrigger>
          <TabsTrigger value="files">
            <Files className="size-3.5" />
            文件{content ? ` (${content.files.length})` : ''}
          </TabsTrigger>
          <TabsTrigger value="workspaces">
            <Network className="size-3.5" />
            Workspace
          </TabsTrigger>
          <TabsTrigger value="versions">
            <History className="size-3.5" />
            版本{versions.length > 0 ? ` (${versions.length})` : ''}
          </TabsTrigger>
          {versions.length > 1 && (
            <TabsTrigger value="compare">
              <GitCompare className="size-3.5" />
              对比
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          {contentLoading && <p className="text-xs text-muted-foreground">加载 SKILL.md…</p>}
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
          {contentLoading && <p className="text-xs text-muted-foreground">加载文件列表…</p>}
          {content && (
            <FilesTab skillId={skill.id} version={skill.current_version} files={content.files} />
          )}
        </TabsContent>

        <TabsContent value="workspaces" className="mt-4">
          <WorkspaceBindingsTable
            skillId={skill.id}
            installed={installed}
            autoBind={skill.auto_bind ?? false}
          />
        </TabsContent>

        <TabsContent value="versions" className="mt-4">
          <VersionsTab skillId={skill.id} skill={skill} onInstalled={handleActionDone} />
        </TabsContent>

        {versions.length > 1 && (
          <TabsContent value="compare" className="mt-4">
            <CompareTab skillId={skill.id} versions={versions} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
