'use client'

import { AlertCircle, Plus, Trash2 } from 'lucide-react'
import type { SandboxCommandRule } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

interface Props {
  rules: SandboxCommandRule[]
  onChange: (next: SandboxCommandRule[]) => void
  disabled?: boolean
}

export function CommandRulesTable({ rules, onChange, disabled }: Props) {
  const update = (idx: number, patch: Partial<SandboxCommandRule>) => {
    onChange(rules.map((r, i) => (i === idx ? { ...r, ...patch } : r)))
  }
  const remove = (idx: number) => onChange(rules.filter((_, i) => i !== idx))
  const add = () => onChange([...rules, { action: 'deny', pattern: '' }])

  const hasConfirm = rules.some((r) => r.action === 'confirm')

  return (
    <div className="flex flex-col gap-2">
      {rules.length === 0 ? (
        <p className="rounded-md border border-dashed border-border/60 bg-muted/20 px-3 py-3 text-center text-xs text-muted-foreground">
          No command rules. All shell commands run unmodified.
        </p>
      ) : (
        <div className="overflow-x-auto rounded border border-border bg-card">
          <div className="grid min-w-[480px] grid-cols-[140px_1fr_44px] items-center gap-2 border-b border-border bg-accent px-3 py-2 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
            <span>Action</span>
            <span>Pattern (glob)</span>
            <span className="sr-only">Remove</span>
          </div>
          <ul className="divide-y divide-border">
            {rules.map((rule, idx) => (
              <li
                key={idx}
                className="grid min-w-[480px] grid-cols-[140px_1fr_44px] items-center gap-2 px-3 py-2"
              >
                <Select
                  value={rule.action}
                  onValueChange={(v) =>
                    update(idx, { action: (v ?? 'deny') as SandboxCommandRule['action'] })
                  }
                  disabled={disabled}
                >
                  <SelectTrigger aria-label="Action">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="allow">allow</SelectItem>
                    <SelectItem value="deny">deny</SelectItem>
                    <SelectItem value="confirm">confirm</SelectItem>
                  </SelectContent>
                </Select>
                <Input
                  value={rule.pattern}
                  onChange={(e) => update(idx, { pattern: e.target.value })}
                  placeholder="rm *"
                  aria-label="Pattern"
                  disabled={disabled}
                  className="font-mono text-xs"
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
      {hasConfirm && (
        <div className="flex items-start gap-2 rounded border border-warning-border bg-warning-surface px-3 py-2 text-xs text-warning-fg">
          <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
          <span>
            <strong className="font-medium">confirm</strong> pauses the agent and asks a human to
            approve or deny the command before it runs. No approval (deny, timeout, or cancel)
            blocks the command.
          </span>
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
          Add command rule
        </Button>
      </div>
    </div>
  )
}
