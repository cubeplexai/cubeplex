'use client'

import { useEffect, useId, useRef } from 'react'
import { useTranslations } from 'next-intl'
import {
  CircleHelp,
  MessageSquarePlus,
  Square,
  Cpu,
  Gauge,
  Pencil,
  Share2,
  Paperclip,
  Sparkles,
  Plug,
  Shrink,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SlashCommand } from '@/lib/slash-commands'
import { ComposerOverlayShell } from './ComposerOverlayShell'

type DescKey =
  | 'commands.help.description'
  | 'commands.new.description'
  | 'commands.stop.description'
  | 'commands.model.description'
  | 'commands.effort.description'
  | 'commands.rename.description'
  | 'commands.share.description'
  | 'commands.attach.description'
  | 'commands.skills.description'
  | 'commands.mcp.description'
  | 'commands.compact.description'

function descriptionFor(cmdId: string): DescKey {
  switch (cmdId) {
    case 'help':
      return 'commands.help.description'
    case 'new':
      return 'commands.new.description'
    case 'stop':
      return 'commands.stop.description'
    case 'model':
      return 'commands.model.description'
    case 'effort':
      return 'commands.effort.description'
    case 'rename':
      return 'commands.rename.description'
    case 'share':
      return 'commands.share.description'
    case 'attach':
      return 'commands.attach.description'
    case 'skills':
      return 'commands.skills.description'
    case 'mcp':
      return 'commands.mcp.description'
    case 'compact':
      return 'commands.compact.description'
    default:
      return 'commands.help.description'
  }
}

const COMMAND_ICONS: Record<string, LucideIcon> = {
  help: CircleHelp,
  new: MessageSquarePlus,
  stop: Square,
  model: Cpu,
  effort: Gauge,
  rename: Pencil,
  share: Share2,
  attach: Paperclip,
  skills: Sparkles,
  mcp: Plug,
  compact: Shrink,
}

export type CommandPopoverProps = {
  open: boolean
  commands: SlashCommand[]
  activeIndex: number
  onActiveIndexChange: (i: number) => void
  onSelect: (cmd: SlashCommand) => void
  listboxId?: string
}

export function CommandPopover({
  open,
  commands,
  activeIndex,
  onActiveIndexChange,
  onSelect,
  listboxId: listboxIdProp,
}: CommandPopoverProps): React.ReactElement | null {
  const t = useTranslations('slashCommands')
  const autoId = useId()
  const listboxId = listboxIdProp ?? autoId
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const el = listRef.current?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`)
    // jsdom may not implement scrollIntoView
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [open, activeIndex, commands])

  if (!open) return null

  const activeId =
    commands.length > 0 && activeIndex >= 0 && activeIndex < commands.length
      ? `${listboxId}-opt-${commands[activeIndex]!.id}`
      : undefined

  return (
    <ComposerOverlayShell
      id={listboxId}
      role="listbox"
      aria-label={t('listAria')}
      aria-activedescendant={activeId}
      data-testid="slash-command-popover"
    >
      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto p-1">
        {commands.length === 0 ? (
          <div
            className="flex h-9 items-center px-2 text-xs text-muted-foreground"
            role="presentation"
          >
            {t('noMatches')}
          </div>
        ) : (
          commands.map((cmd, i) => {
            const active = i === activeIndex
            const optionId = `${listboxId}-opt-${cmd.id}`
            const Icon = COMMAND_ICONS[cmd.id] ?? CircleHelp
            return (
              <button
                key={cmd.id}
                id={optionId}
                type="button"
                role="option"
                aria-selected={active}
                data-index={i}
                data-testid={`slash-cmd-${cmd.name}`}
                onMouseEnter={() => onActiveIndexChange(i)}
                onClick={() => onSelect(cmd)}
                className={cn(
                  'flex h-9 w-full items-center gap-2.5 rounded-md px-2 text-left text-sm transition-colors',
                  active ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60',
                )}
              >
                <span
                  className={cn(
                    'flex size-5 shrink-0 items-center justify-center text-muted-foreground',
                    active && 'text-accent-foreground',
                  )}
                >
                  <Icon className="size-3.5" aria-hidden />
                </span>
                <span className="shrink-0 font-medium">/{cmd.name}</span>
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  {t(descriptionFor(cmd.id))}
                </span>
              </button>
            )
          })
        )}
      </div>
      <div className="shrink-0 border-t border-border px-2.5 py-1.5 text-[11px] text-muted-foreground">
        {t('filterHint')}
      </div>
    </ComposerOverlayShell>
  )
}
