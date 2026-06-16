'use client'

import { useTranslations } from 'next-intl'
import { MoreHorizontal, Plus, Trash2, Webhook } from 'lucide-react'
import { type Trigger } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/EmptyState'
import { RailCard } from '@/components/shared/RailCard'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

interface TriggersListProps {
  triggers: Trigger[]
  loading: boolean
  onToggleEnabled: (id: string, enabled: boolean) => Promise<void>
  onDelete: (id: string) => void
  onCreate: () => void
  selectedId?: string | null
  onSelect?: (id: string) => void
}

export function TriggersList({
  triggers,
  loading,
  onToggleEnabled,
  onDelete,
  onCreate,
  selectedId,
  onSelect,
}: TriggersListProps) {
  const t = useTranslations('triggers')

  if (loading) {
    return <div className="py-10 text-center text-xs text-muted-foreground">{t('loading')}</div>
  }

  if (triggers.length === 0) {
    return (
      <EmptyState
        icon={Webhook}
        title={t('emptyTitle')}
        description={t('emptyHint')}
        data-testid="triggers-empty"
        action={
          <Button size="sm" className="gap-1.5" onClick={onCreate}>
            <Plus className="size-3.5" />
            {t('createTrigger')}
          </Button>
        }
      />
    )
  }

  return (
    <div className="flex flex-col gap-2">
      {triggers.map((trigger) => (
        <RailCard
          key={trigger.id}
          data-testid={`trigger-row-${trigger.id}`}
          title={<span data-testid={`trigger-link-${trigger.id}`}>{trigger.name}</span>}
          selected={selectedId === trigger.id}
          onSelect={onSelect ? () => onSelect(trigger.id) : undefined}
          badge={
            trigger.enabled ? (
              <Badge
                variant="default"
                className="text-xs bg-success-solid/15 text-success-fg border-success-border hover:bg-success-solid/15"
              >
                {t('statusEnabled')}
              </Badge>
            ) : (
              <Badge variant="secondary" className="text-xs">
                {t('statusDisabled')}
              </Badge>
            )
          }
          secondary={trigger.source_type}
          meta={t('cardEvents', { count: trigger.events_total })}
          actions={
            <DropdownMenu>
              <DropdownMenuTrigger className="inline-flex h-6 w-6 items-center justify-center rounded-md p-0 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground group-hover:opacity-100 data-[popup-open]:opacity-100">
                <MoreHorizontal className="size-3.5" />
                <span className="sr-only">{t('colActions')}</span>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() => void onToggleEnabled(trigger.id, !trigger.enabled)}
                >
                  {trigger.enabled ? t('actionDisable') : t('actionEnable')}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onClick={() => onDelete(trigger.id)}>
                  <Trash2 className="size-3.5 mr-1.5" />
                  {t('delete')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          }
        />
      ))}
    </div>
  )
}
