'use client'

import { Plus, Trash2 } from 'lucide-react'
import type { SandboxNetworkRule } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

interface Props {
  rules: SandboxNetworkRule[]
  defaultAction: 'allow' | 'deny'
  onChangeDefaultAction: (v: 'allow' | 'deny') => void
  onChange: (next: SandboxNetworkRule[]) => void
  disabled?: boolean
}

export function NetworkRulesTable({
  rules,
  defaultAction,
  onChangeDefaultAction,
  onChange,
  disabled,
}: Props) {
  const update = (idx: number, patch: Partial<SandboxNetworkRule>) => {
    onChange(rules.map((r, i) => (i === idx ? { ...r, ...patch } : r)))
  }
  const remove = (idx: number) => onChange(rules.filter((_, i) => i !== idx))
  const add = () => onChange([...rules, { action: 'deny', target: '' }])

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3 rounded-md border border-border/60 bg-muted/20 px-3 py-2">
        <div className="flex flex-col">
          <span className="text-xs font-medium">Default action</span>
          <span className="text-[11px] text-muted-foreground">
            {defaultAction === 'allow'
              ? 'Sandboxes can reach any host except the deny rules below.'
              : 'Sandboxes are blocked from every host except the allow rules below.'}
          </span>
        </div>
        <div className="inline-flex overflow-hidden rounded-md border border-border/70">
          {(['allow', 'deny'] as const).map((opt) => (
            <button
              key={opt}
              type="button"
              disabled={disabled}
              onClick={() => onChangeDefaultAction(opt)}
              aria-pressed={defaultAction === opt}
              className={cn(
                'px-3 py-1 text-xs font-medium transition-colors',
                defaultAction === opt
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-transparent text-muted-foreground hover:bg-muted/50',
              )}
            >
              {opt}
            </button>
          ))}
        </div>
      </div>
      {rules.length === 0 ? (
        <p className="rounded-md border border-dashed border-border/60 bg-muted/20 px-3 py-3 text-center text-xs text-muted-foreground">
          {defaultAction === 'allow' ? (
            <>
              No deny rules. All outbound traffic is allowed — add{' '}
              <span className="font-medium">deny</span> rules for hosts to block.
            </>
          ) : (
            <>
              No allow rules. Outbound traffic is denied by default — add{' '}
              <span className="font-medium">allow</span> rules for the hosts your sandboxes need to
              reach.
            </>
          )}
        </p>
      ) : (
        <div className="overflow-hidden rounded-md border border-border/70 bg-card/40">
          <div className="grid grid-cols-[140px_1fr_44px] items-center gap-2 border-b border-border/60 bg-muted/30 px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            <span>Action</span>
            <span>Target (host or FQDN)</span>
            <span className="sr-only">Remove</span>
          </div>
          <ul className="divide-y divide-border/40">
            {rules.map((rule, idx) => (
              <li
                key={idx}
                className="grid grid-cols-[140px_1fr_44px] items-center gap-2 px-3 py-2"
              >
                <Select
                  value={rule.action}
                  onValueChange={(v) =>
                    update(idx, { action: (v ?? 'deny') as SandboxNetworkRule['action'] })
                  }
                  disabled={disabled}
                >
                  <SelectTrigger aria-label="Action">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="allow">allow</SelectItem>
                    <SelectItem value="deny">deny</SelectItem>
                  </SelectContent>
                </Select>
                <Input
                  value={rule.target}
                  onChange={(e) => update(idx, { target: e.target.value })}
                  placeholder="api.github.com"
                  aria-label="Target"
                  disabled={disabled}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => remove(idx)}
                  disabled={disabled}
                  aria-label={`Remove rule ${idx + 1}`}
                  className="h-8 w-8 text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div>
        <Button
          variant="outline"
          size="sm"
          onClick={add}
          disabled={disabled}
          className="h-7 gap-1.5 text-xs"
        >
          <Plus className="size-3" />
          Add network rule
        </Button>
      </div>
    </div>
  )
}
