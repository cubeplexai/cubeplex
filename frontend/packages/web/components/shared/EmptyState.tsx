import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface EmptyStateProps {
  icon?: LucideIcon
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
  'data-testid'?: string
}

/**
 * Shared empty-state card: dashed border, centered icon/title/description and
 * an optional call-to-action. Use it instead of ad-hoc "no X yet" divs so
 * empty screens look consistent and always offer a next step.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
  'data-testid': testId,
}: EmptyStateProps): React.ReactElement {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed',
        'border-border/70 bg-muted/20 px-6 py-12 text-center',
        className,
      )}
      data-testid={testId}
    >
      {Icon && (
        <div className="mb-1 flex size-10 items-center justify-center rounded-full bg-muted/60">
          <Icon className="size-5 text-muted-foreground" aria-hidden />
        </div>
      )}
      <p className="text-sm font-medium text-muted-foreground">{title}</p>
      {description && <p className="max-w-md text-xs text-muted-foreground/70">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
