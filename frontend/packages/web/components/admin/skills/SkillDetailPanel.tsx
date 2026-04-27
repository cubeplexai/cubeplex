'use client'

import useSWR from 'swr'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { SkillContent } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
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

  const installed =
    skill.install_state === 'installed' || skill.install_state === 'update_available'

  return (
    <div className="flex w-full flex-col gap-6 p-6" data-testid="skill-detail-panel">
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

      <Separator />

      <section>
        <h4 className="mb-2 text-sm font-medium uppercase tracking-wide text-muted-foreground/80">
          Workspace 绑定
        </h4>
        <WorkspaceBindingsTable skillId={skill.id} installed={installed} />
      </section>

      <Separator />

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h4 className="text-sm font-medium uppercase tracking-wide text-muted-foreground/80">
            SKILL.md
          </h4>
          {content && (
            <span className="text-[11px] text-muted-foreground/70">
              {content.files.length} 个文件
            </span>
          )}
        </div>
        {contentLoading && <p className="text-xs text-muted-foreground">加载 SKILL.md…</p>}
        {content && (
          <div className="rounded-lg border border-border/70 bg-card/40 px-4 py-3">
            <div className={proseClasses}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{content.content}</ReactMarkdown>
            </div>
          </div>
        )}
      </section>

      {skill.versions.length > 1 && (
        <section>
          <h4 className="mb-2 text-sm font-medium uppercase tracking-wide text-muted-foreground/80">
            版本
          </h4>
          <ul className="flex flex-col gap-1">
            {skill.versions.map((v) => (
              <li
                key={v.id}
                className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2 text-xs"
              >
                <span className="font-mono">v{v.version}</span>
                <span className="text-muted-foreground">
                  {new Date(v.created_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
