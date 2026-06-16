import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface EmptyStateProps {
  icon?: LucideIcon
  title: string
  description?: string
  action?: React.ReactNode
  /** `sm` is a lighter variant for empties shown inside a detail panel. */
  size?: 'default' | 'sm'
  className?: string
  'data-testid'?: string
}

/**
 * Shared empty-state card: dashed border, centered icon/title/description and
 * an optional call-to-action. Always full width (so it never shrink-wraps to
 * its text). Use it instead of ad-hoc "no X yet" divs so empty screens look
 * consistent and always offer a next step.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  size = 'default',
  className,
  'data-testid': testId,
}: EmptyStateProps): React.ReactElement {
  const sm = size === 'sm'
  return (
    <div
      className={cn(
        'flex w-full flex-col items-center justify-center gap-2 rounded-xl border border-dashed',
        'border-border/70 bg-muted/20 text-center',
        sm ? 'px-4 py-8' : 'px-6 py-12',
        className,
      )}
      data-testid={testId}
    >
      {Icon && (
        <div
          className={cn(
            'mb-1 flex items-center justify-center rounded-full bg-muted/60',
            sm ? 'size-8' : 'size-10',
          )}
        >
          <Icon className={cn('text-muted-foreground', sm ? 'size-4' : 'size-5')} aria-hidden />
        </div>
      )}
      <p className="text-sm font-medium text-muted-foreground">{title}</p>
      {description && <p className="max-w-md text-xs text-muted-foreground/70">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
