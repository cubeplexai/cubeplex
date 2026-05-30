'use client'

import { Database, ShieldCheck, ShieldAlert, ShieldOff } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SkillRegistryEntry } from '@/hooks/useAdminSkillRegistries'

function KindBadge({ kind }: { kind: string }) {
  return (
    <span className="rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      {kind === 'skills-sh' ? 'skills.sh' : 'Custom'}
    </span>
  )
}

function TrustBadge({ tier }: { tier: string }) {
  if (tier === 'official') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
        <ShieldCheck className="size-3" />
        Official
      </span>
    )
  }
  if (tier === 'community') {
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

interface RegistryCardProps {
  registry: SkillRegistryEntry
  active: boolean
  onClick: () => void
}

export function RegistryCard({ registry, active, onClick }: RegistryCardProps) {
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
        !registry.enabled && 'opacity-60',
      )}
    >
      <div className="flex items-center gap-2">
        <Database className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate text-sm font-semibold">{registry.name}</span>
        <KindBadge kind={registry.kind} />
      </div>
      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        <TrustBadge tier={registry.trust_tier} />
        {!registry.enabled && (
          <span className="text-[10px] text-muted-foreground/70">disabled</span>
        )}
      </div>
    </button>
  )
}
