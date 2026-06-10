'use client'

import { CheckCircle2, CircleSlash, Lock, Package, Plus, Sparkles } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { WorkspaceSkillEntry, WorkspaceSkillState } from '@/hooks/useWorkspaceSkillsCatalog'

interface WorkspaceSkillCardProps {
  skill: WorkspaceSkillEntry
  active: boolean
  onClick: () => void
}

function StateBadge({ state }: { state: WorkspaceSkillState }) {
  const t = useTranslations('wsSettings.skillCard')
  if (state === 'org-enabled') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-success-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-success-fg">
        <CheckCircle2 className="size-3" />
        {t('enabled')}
      </span>
    )
  }
  if (state === 'org-disabled') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
        <CircleSlash className="size-3" />
        {t('disabled')}
      </span>
    )
  }
  if (state === 'workspace-private') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
        <Lock className="size-3" />
        {t('private')}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-warning-solid/10 px-1.5 py-0.5 text-[10px] font-medium text-warning-fg">
      <Plus className="size-3" />
      {t('available')}
    </span>
  )
}

export function WorkspaceSkillCard({ skill, active, onClick }: WorkspaceSkillCardProps) {
  const SourceIcon = skill.source === 'preinstalled' ? Sparkles : Package
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex items-center gap-2">
        <SourceIcon
          className={cn(
            'size-3.5 shrink-0',
            skill.source === 'preinstalled' ? 'text-primary' : 'text-muted-foreground',
          )}
        />
        <span className="truncate text-sm font-semibold">{skill.name}</span>
        <StateBadge state={skill.workspaceState} />
      </div>
      {skill.description && (
        <p className="line-clamp-2 text-xs text-muted-foreground">{skill.description}</p>
      )}
      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        <span className="font-mono text-[10px] text-muted-foreground/80">
          v{skill.installed_version ?? skill.current_version}
        </span>
        {skill.keywords.slice(0, 2).map((kw) => (
          <Badge key={kw} variant="outline" className="px-1.5 text-[10px]">
            {kw}
          </Badge>
        ))}
        {skill.keywords.length > 2 && (
          <span className="text-[10px] text-muted-foreground/70">+{skill.keywords.length - 2}</span>
        )}
      </div>
    </button>
  )
}
