import { Brain, Pencil, Trash2 } from 'lucide-react'
import type { Model } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface ModelRowProps {
  model: Model
  onEdit: (model: Model) => void
  onDelete: (model: Model) => void
}

function formatCost(cost: number): string {
  if (cost === 0) return '0'
  if (cost < 0.01) return cost.toFixed(6)
  return cost.toFixed(4)
}

export function ModelRow({ model, onEdit, onDelete }: ModelRowProps) {
  return (
    <div
      data-testid={`model-row-${model.model_id}`}
      className={cn(
        'flex items-center gap-3 rounded-lg border px-3 py-2.5 text-xs transition-colors',
        model.is_system
          ? 'border-border/40 bg-muted/20'
          : 'border-border/70 bg-card/40 hover:bg-accent/30',
      )}
    >
      {/* Model ID */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono font-medium text-foreground">{model.model_id}</span>
          {model.reasoning && (
            <Brain className="size-3.5 text-purple-500 shrink-0" aria-label="reasoning" />
          )}
          {model.is_system && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
              系统
            </Badge>
          )}
        </div>
        {model.display_name && model.display_name !== model.model_id && (
          <span className="mt-0.5 block text-muted-foreground">{model.display_name}</span>
        )}
      </div>

      {/* Input Modalities */}
      <div className="hidden sm:flex items-center gap-1 shrink-0">
        {model.input_modalities.map((mod) => (
          <Badge key={mod} variant="outline" className="text-[10px] px-1.5">
            {mod}
          </Badge>
        ))}
      </div>

      {/* Context Window */}
      <span className="hidden md:block shrink-0 text-muted-foreground min-w-[60px] text-right">
        {model.context_window > 0 ? `${(model.context_window / 1000).toFixed(0)}K` : '-'}
      </span>

      {/* Costs */}
      <span className="hidden lg:block shrink-0 text-muted-foreground min-w-[100px] text-right">
        {model.cost_input > 0 || model.cost_output > 0
          ? `$${formatCost(model.cost_input)}/$${formatCost(model.cost_output)} per 1M`
          : '-'}
      </span>

      {/* Actions */}
      <div className="flex items-center gap-1 shrink-0">
        {!model.is_system && (
          <>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => onEdit(model)}
              aria-label={`Edit ${model.model_id}`}
            >
              <Pencil className="size-3" />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              onClick={() => onDelete(model)}
              aria-label={`Delete ${model.model_id}`}
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="size-3" />
            </Button>
          </>
        )}
      </div>
    </div>
  )
}
