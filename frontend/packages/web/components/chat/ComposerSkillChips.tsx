'use client'

import { Sparkles, X } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { formatSkillLabel } from '@cubeplex/core'
import { cn } from '@/lib/utils'
import type { ComposerSkillChip } from '@/lib/composer/skillChips'

type ComposerSkillChipsProps = {
  skills: ComposerSkillChip[]
  onRemove: (id: string) => void
  className?: string
}

export function ComposerSkillChips({
  skills,
  onRemove,
  className,
}: ComposerSkillChipsProps): React.ReactElement | null {
  const t = useTranslations('composerExtras')
  if (skills.length === 0) return null

  return (
    <div
      className={cn('flex flex-wrap gap-1.5', className)}
      data-testid="composer-skill-chips"
      aria-label={t('skillChipsAria')}
    >
      {skills.map((skill) => {
        const label = formatSkillLabel(skill.name)
        return (
          <span
            key={skill.id}
            data-testid={`composer-skill-chip-${skill.name}`}
            title={label.canonical}
            className={cn(
              'inline-flex h-6 max-w-full items-center gap-1 rounded-md border border-border bg-muted/60',
              'py-0.5 pr-0.5 pl-1.5 text-xs text-foreground',
            )}
          >
            <Sparkles className="size-3 shrink-0 text-muted-foreground" aria-hidden />
            <span className="max-w-36 truncate font-medium">{label.primary}</span>
            <button
              type="button"
              onClick={() => onRemove(skill.id)}
              aria-label={t('removeSkill', { name: label.primary })}
              className="flex size-5 shrink-0 items-center justify-center rounded hover:bg-accent"
            >
              <X className="size-3" />
            </button>
          </span>
        )
      })}
    </div>
  )
}
