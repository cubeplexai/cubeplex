'use client'

import { useState } from 'react'
import useSWR from 'swr'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText, Files, GitCompare, History, Network } from 'lucide-react'
import type { SkillContent } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { useAdminSkill } from '@/hooks/useAdminSkill'
import { proseClasses } from '@/lib/utils'
import { OrgInstallActions } from './OrgInstallActions'
import { WorkspaceBindingsTable } from './WorkspaceBindingsTable'

interface SkillDetailPanelProps {
  skillId: string | null
  onActionDone: () => void
}

const contentFetcher = async (url: string): Promise<SkillContent> => {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`skill content fetch failed: ${res.status}`)
  return res.json() as Promise<SkillContent>
}

function stripFrontmatter(content: string): string {
  return content.replace(/^---\s*\n[\s\S]*?\n---\s*(\n|$)/, '')
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function SkillDetailPanel({ skillId, onActionDone }: SkillDetailPanelProps) {
  const { skill, loading, error, refresh } = useAdminSkill(skillId)
  const [compareLeft, setCompareLeft] = useState<string>('')
  const [compareRight, setCompareRight] = useState<string>('')

  const contentKey =
    skill && skill.current_version
      ? `/api/v1/admin/skills/${skill.id}/versions/${skill.current_version}`
      : null
  const { data: content, isLoading: contentLoading } = useSWR<SkillContent>(
    contentKey,
    contentFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  const compareLeftKey =
    skill && compareLeft ? `/api/v1/admin/skills/${skill.id}/versions/${compareLeft}` : null
  const compareRightKey =
    skill && compareRight ? `/api/v1/admin/skills/${skill.id}/versions/${compareRight}` : null
  const { data: leftContent } = useSWR<SkillContent>(compareLeftKey, contentFetcher, {
    revalidateOnFocus: false,
  })
  const { data: rightContent } = useSWR<SkillContent>(compareRightKey, contentFetcher, {
    revalidateOnFocus: false,
  })

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

  const installed =
    skill.install_state === 'installed' || skill.install_state === 'update_available'

  const versions = skill.versions.slice().sort((a, b) => b.version.localeCompare(a.version))

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

      {/* Org-level install actions */}
      <section>
        <h4 className="mb-2 text-sm font-medium uppercase tracking-wide text-muted-foreground/80">
          组织级安装
        </h4>
        <OrgInstallActions
          skill={skill}
          onActionDone={() => {
            void refresh()
            onActionDone()
          }}
        />
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

        {/* Overview — SKILL.md without frontmatter */}
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

        {/* Files tab */}
        <TabsContent value="files" className="mt-4">
          {contentLoading && <p className="text-xs text-muted-foreground">加载文件列表…</p>}
          {content && content.files.length === 0 && (
            <p className="text-xs text-muted-foreground">该版本无附加文件。</p>
          )}
          {content && content.files.length > 0 && (
            <ul className="flex flex-col divide-y divide-border/60 rounded-lg border border-border/70">
              {content.files.map((f) => (
                <li
                  key={f.rel_path}
                  className="flex items-center justify-between px-3 py-2 text-xs"
                >
                  <span className="truncate font-mono text-foreground/80">{f.rel_path}</span>
                  <span className="shrink-0 pl-4 text-muted-foreground">{formatBytes(f.size)}</span>
                </li>
              ))}
            </ul>
          )}
        </TabsContent>

        {/* Workspaces tab */}
        <TabsContent value="workspaces" className="mt-4">
          <WorkspaceBindingsTable
            skillId={skill.id}
            installed={installed}
            autoBind={skill.auto_bind ?? false}
          />
        </TabsContent>

        {/* Versions tab */}
        <TabsContent value="versions" className="mt-4">
          {versions.length === 0 && <p className="text-xs text-muted-foreground">暂无版本记录。</p>}
          <ul className="flex flex-col gap-1.5">
            {versions.map((v) => (
              <li
                key={v.id}
                className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2.5 text-xs"
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono font-semibold">v{v.version}</span>
                  {v.version === skill.installed_version && (
                    <Badge
                      variant="outline"
                      className="border-emerald-500/40 text-[10px] text-emerald-600"
                    >
                      已安装
                    </Badge>
                  )}
                  {v.version === skill.current_version && v.version !== skill.installed_version && (
                    <Badge variant="outline" className="text-[10px]">
                      最新
                    </Badge>
                  )}
                </div>
                <span className="text-muted-foreground">
                  {new Date(v.created_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        </TabsContent>

        {/* Compare tab */}
        {versions.length > 1 && (
          <TabsContent value="compare" className="mt-4">
            <div className="mb-3 flex items-center gap-3">
              <div className="flex flex-1 flex-col gap-1">
                <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70">
                  版本 A
                </label>
                <select
                  value={compareLeft}
                  onChange={(e) => setCompareLeft(e.target.value)}
                  className="rounded-md border border-border/70 bg-background px-2 py-1.5 text-xs"
                >
                  <option value="">选择版本…</option>
                  {versions.map((v) => (
                    <option key={v.id} value={v.version}>
                      v{v.version}
                    </option>
                  ))}
                </select>
              </div>
              <span className="mt-4 text-muted-foreground">vs</span>
              <div className="flex flex-1 flex-col gap-1">
                <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground/70">
                  版本 B
                </label>
                <select
                  value={compareRight}
                  onChange={(e) => setCompareRight(e.target.value)}
                  className="rounded-md border border-border/70 bg-background px-2 py-1.5 text-xs"
                >
                  <option value="">选择版本…</option>
                  {versions.map((v) => (
                    <option key={v.id} value={v.version}>
                      v{v.version}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {compareLeft && compareRight && (
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] font-medium text-muted-foreground/70">
                    v{compareLeft}
                  </span>
                  <div className="max-h-[50vh] overflow-y-auto rounded-lg border border-border/70 bg-card/40 px-4 py-3">
                    <div className={proseClasses}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {leftContent ? stripFrontmatter(leftContent.content) : '加载中…'}
                      </ReactMarkdown>
                    </div>
                  </div>
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-[11px] font-medium text-muted-foreground/70">
                    v{compareRight}
                  </span>
                  <div className="max-h-[50vh] overflow-y-auto rounded-lg border border-border/70 bg-card/40 px-4 py-3">
                    <div className={proseClasses}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {rightContent ? stripFrontmatter(rightContent.content) : '加载中…'}
                      </ReactMarkdown>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {(!compareLeft || !compareRight) && (
              <p className="text-xs text-muted-foreground">选择两个版本进行对比。</p>
            )}
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
