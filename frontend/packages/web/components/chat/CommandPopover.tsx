'use client'

import { useEffect, useId, useRef } from 'react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import type { SlashCommand } from '@/lib/slash-commands'

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
    <div
      ref={listRef}
      id={listboxId}
      role="listbox"
      aria-label={t('listAria')}
      aria-activedescendant={activeId}
      data-testid="slash-command-popover"
      className={cn(
        'absolute bottom-full left-0 right-0 z-50 mb-1 max-h-64 overflow-y-auto',
        'rounded-lg border border-border bg-popover py-1 shadow-md',
      )}
    >
      {commands.length === 0 ? (
        <div className="px-3 py-2 text-sm text-muted-foreground" role="presentation">
          {t('noMatches')}
        </div>
      ) : (
        commands.map((cmd, i) => {
          const active = i === activeIndex
          const optionId = `${listboxId}-opt-${cmd.id}`
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
                'flex w-full items-start gap-2 px-3 py-1.5 text-left text-sm transition-colors',
                active ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60',
              )}
            >
              <span className="shrink-0 font-mono font-medium text-primary">/{cmd.name}</span>
              <span className="min-w-0 flex-1 text-muted-foreground">
                {t(descriptionFor(cmd.id))}
              </span>
            </button>
          )
        })
      )}
    </div>
  )
}
