'use client'

import { useTranslations } from 'next-intl'
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
  const t = useTranslations('adminSkills')
  if (state === 'installed') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-success-surface px-1.5 py-0.5 text-[10px] font-medium text-success-fg transition-colors">
        <CheckCircle2 className="size-3" />
        {t('installed')}
      </span>
    )
  }
  if (state === 'update_available') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning-surface px-1.5 py-0.5 text-[10px] font-medium text-warning-fg">
        <ArrowUpCircle className="size-3" />
        {t('upgradable')}
      </span>
    )
  }
  return null
}

export function SkillCard({ skill, active, onClick }: SkillCardProps) {
  const t = useTranslations('adminSkills')
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
            <span className="font-mono text-[10px] text-warning-fg">
              {t('installedVersion', { version: skill.installed_version })}
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
