'use client'

import { Paperclip, Plus, Plug, Sparkles } from 'lucide-react'
import { useTranslations } from 'next-intl'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

export type ComposerAddMenuProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  disabled?: boolean
  canAttach: boolean
  canSkills: boolean
  canMcp: boolean
  onAttach: () => void
  onSkills: () => void
  onMcp: () => void
}

export function ComposerAddMenu({
  open,
  onOpenChange,
  disabled = false,
  canAttach,
  canSkills,
  canMcp,
  onAttach,
  onSkills,
  onMcp,
}: ComposerAddMenuProps): React.ReactElement {
  const t = useTranslations('composerExtras')

  return (
    <DropdownMenu open={open} onOpenChange={onOpenChange}>
      <DropdownMenuTrigger
        type="button"
        disabled={disabled}
        aria-label={t('addMenuAria')}
        data-testid="composer-add-menu"
        className={cn(
          'grid size-7 shrink-0 place-items-center rounded-lg text-muted-foreground',
          'transition-[background-color,color,transform] duration-150',
          'hover:bg-accent hover:text-foreground active:scale-[0.94]',
          'outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
          'disabled:cursor-not-allowed disabled:opacity-30',
          open && 'bg-accent text-foreground',
        )}
      >
        <Plus className="size-3.5" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" sideOffset={8} className="min-w-56 p-1">
        <DropdownMenuItem
          disabled={!canAttach}
          data-testid="composer-add-upload"
          className="items-start py-2"
          onClick={() => {
            onAttach()
          }}
        >
          <Paperclip className="mt-0.5 size-3.5" aria-hidden />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="text-sm font-medium leading-none">{t('uploadFiles')}</span>
            <span className="text-[11px] leading-snug text-muted-foreground">
              {t('uploadFilesDesc')}
            </span>
          </span>
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!canSkills}
          data-testid="composer-add-skills"
          className="items-start py-2"
          onClick={() => {
            onSkills()
          }}
        >
          <Sparkles className="mt-0.5 size-3.5" aria-hidden />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="text-sm font-medium leading-none">{t('skills')}</span>
            <span className="text-[11px] leading-snug text-muted-foreground">
              {t('skillsDesc')}
            </span>
          </span>
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!canMcp}
          data-testid="composer-add-mcp"
          className="items-start py-2"
          onClick={() => {
            onMcp()
          }}
        >
          <Plug className="mt-0.5 size-3.5" aria-hidden />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="text-sm font-medium leading-none">{t('mcp')}</span>
            <span className="text-[11px] leading-snug text-muted-foreground">{t('mcpDesc')}</span>
          </span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
