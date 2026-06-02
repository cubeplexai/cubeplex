'use client'

import { Globe, ShieldCheck, ShieldAlert, ShieldOff } from 'lucide-react'
import type { SkillCandidateOut } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

function TrustBadge({ trust }: { trust: SkillCandidateOut['trust'] }) {
  if (trust === 'official') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
        <ShieldCheck className="size-3" />
        Official
      </span>
    )
  }
  if (trust === 'community') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-medium text-blue-600 dark:text-blue-400">
        <ShieldAlert className="size-3" />
        Community
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
      <ShieldOff className="size-3" />
      Unvetted
    </span>
  )
}

function getOfficialSource(repo: string | null): string | null {
  if (!repo) return null
  const match = repo.match(/github\.com\/([^/]+)/)
  return match ? match[1] : null
}

interface CandidateCardProps {
  candidate: SkillCandidateOut
  active: boolean
  onClick: () => void
}

export function CandidateCard({ candidate, active, onClick }: CandidateCardProps) {
  return (
    <button
      type="button"
      data-testid="skill-candidate-card"
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
        <Globe className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate text-sm font-semibold">{candidate.name}</span>
        <TrustBadge trust={candidate.trust} />
      </div>
      {candidate.description && (
        <p className="line-clamp-2 text-xs text-muted-foreground">{candidate.description}</p>
      )}
      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <span className="rounded-md bg-gradient-to-r from-slate-500/15 to-slate-500/5 border border-slate-500/20 px-2 py-0.5 text-[10px] font-semibold text-slate-700 dark:text-slate-300">
          {candidate.source_name}
        </span>
        {candidate.version && (
          <span className="font-mono text-[10px] text-muted-foreground/80">
            v{candidate.version}
          </span>
        )}
        {candidate.install_count !== null && (
          <span className="text-[10px] text-muted-foreground/80">
            {candidate.install_count.toLocaleString()} installs
          </span>
        )}
        {candidate.trust === 'official' && getOfficialSource(candidate.repo) && (
          <span className="text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
            {getOfficialSource(candidate.repo)}
          </span>
        )}
        {candidate.keywords.slice(0, 2).map((kw) => (
          <Badge key={kw} variant="outline" className="px-1.5 text-[10px]">
            {kw}
          </Badge>
        ))}
        {candidate.keywords.length > 2 && (
          <span className="text-[10px] text-muted-foreground/70">
            +{candidate.keywords.length - 2}
          </span>
        )}
      </div>
    </button>
  )
}
