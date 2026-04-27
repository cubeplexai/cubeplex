'use client'

import { CheckCircle2, ArrowUpCircle, Package, Sparkles } from 'lucide-react'
import type { SkillSummary } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface SkillCardProps {
  skill: SkillSummary
  active: boolean
  onClick: () => void
}

function StateBadge({ state }: { state: SkillSummary['install_state'] }) {
  if (state === 'installed') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 transition-colors group-hover/skill-card:bg-emerald-500/20 dark:text-emerald-400">
        <CheckCircle2 className="size-3" />
        已安装
      </span>
    )
  }
  if (state === 'update_available') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
        <ArrowUpCircle className="size-3" />
        可升级
      </span>
    )
  }
  return null
}

export function SkillCard({ skill, active, onClick }: SkillCardProps) {
  const SourceIcon = skill.source === 'preinstalled' ? Sparkles : Package
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`skill-card-${skill.name}`}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group/skill-card flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
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
        <StateBadge state={skill.install_state} />
      </div>
      {skill.description && (
        <p className="line-clamp-2 text-xs text-muted-foreground">{skill.description}</p>
      )}
      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        <span className="font-mono text-[10px] text-muted-foreground/80">
          v{skill.current_version}
        </span>
        {skill.installed_version &&
          skill.installed_version !== skill.current_version &&
          skill.install_state !== 'uninstalled' && (
            <span className="font-mono text-[10px] text-amber-600 dark:text-amber-400">
              (已装 v{skill.installed_version})
            </span>
          )}
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
