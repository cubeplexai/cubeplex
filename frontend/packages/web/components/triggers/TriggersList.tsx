'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { MoreHorizontal, Plus, Trash2, Webhook } from 'lucide-react'
import { type Trigger } from '@cubebox/core'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/shared/EmptyState'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

interface TriggersListProps {
  wsId: string
  triggers: Trigger[]
  loading: boolean
  onToggleEnabled: (id: string, enabled: boolean) => Promise<void>
  onDelete: (id: string) => void
  onCreate: () => void
  selectedId?: string | null
  onSelect?: (id: string) => void
  compact?: boolean
}

export function TriggersList({
  wsId,
  triggers,
  loading,
  onToggleEnabled,
  onDelete,
  onCreate,
  selectedId,
  onSelect,
  compact,
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
    <div className="rounded-xl border border-border/70 bg-card/40 shadow-sm">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="text-xs">{t('colName')}</TableHead>
            <TableHead className="text-xs">{t('colStatus')}</TableHead>
            {!compact && <TableHead className="text-xs">{t('colSource')}</TableHead>}
            {!compact && <TableHead className="text-xs">{t('colCounters')}</TableHead>}
            <TableHead className="text-xs w-[80px]">{t('colActions')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {triggers.map((trigger) => (
            <TriggerRow
              key={trigger.id}
              trigger={trigger}
              wsId={wsId}
              isSelected={selectedId === trigger.id}
              onSelect={onSelect}
              compact={compact}
              onToggleEnabled={onToggleEnabled}
              onDelete={onDelete}
              t={t}
            />
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

interface TriggerRowProps {
  trigger: Trigger
  wsId: string
  isSelected?: boolean
  onSelect?: (id: string) => void
  compact?: boolean
  onToggleEnabled: (id: string, enabled: boolean) => Promise<void>
  onDelete: (id: string) => void
  t: ReturnType<typeof useTranslations<'triggers'>>
}

function TriggerRow({
  trigger,
  wsId,
  isSelected,
  onSelect,
  compact,
  onToggleEnabled,
  onDelete,
  t,
}: TriggerRowProps) {
  const router = useRouter()

  return (
    <TableRow
      data-testid={`trigger-row-${trigger.id}`}
      className={cn(onSelect && 'cursor-pointer', isSelected && 'bg-muted/40 hover:bg-muted/40')}
      onClick={() => onSelect?.(trigger.id)}
    >
      <TableCell className="text-sm font-medium">
        {onSelect ? (
          <span data-testid={`trigger-link-${trigger.id}`}>{trigger.name}</span>
        ) : (
          <Link
            href={`/w/${wsId}/triggers/${trigger.id}`}
            className="hover:underline text-foreground"
            data-testid={`trigger-link-${trigger.id}`}
            onClick={(e) => e.stopPropagation()}
          >
            {trigger.name}
          </Link>
        )}
      </TableCell>
      <TableCell>
        {trigger.enabled ? (
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
        )}
      </TableCell>
      {!compact && (
        <TableCell className="text-xs text-muted-foreground">{trigger.source_type}</TableCell>
      )}
      {!compact && (
        <TableCell
          className="text-xs text-muted-foreground"
          data-testid={`trigger-counters-${trigger.id}`}
        >
          {trigger.events_total} / {trigger.events_success} / {trigger.events_failed} /{' '}
          {trigger.events_dedup_dropped}
        </TableCell>
      )}
      <TableCell onClick={(e) => e.stopPropagation()}>
        <DropdownMenu>
          <DropdownMenuTrigger className="inline-flex h-6 w-6 items-center justify-center rounded-md p-0 text-muted-foreground hover:bg-accent hover:text-foreground">
            <MoreHorizontal className="size-3.5" />
            <span className="sr-only">{t('colActions')}</span>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {!onSelect && (
              <DropdownMenuItem onClick={() => router.push(`/w/${wsId}/triggers/${trigger.id}`)}>
                {t('actionView')}
              </DropdownMenuItem>
            )}
            <DropdownMenuItem onClick={() => void onToggleEnabled(trigger.id, !trigger.enabled)}>
              {trigger.enabled ? t('actionDisable') : t('actionEnable')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onClick={() => onDelete(trigger.id)}>
              <Trash2 className="size-3.5 mr-1.5" />
              {t('delete')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </TableCell>
    </TableRow>
  )
}
