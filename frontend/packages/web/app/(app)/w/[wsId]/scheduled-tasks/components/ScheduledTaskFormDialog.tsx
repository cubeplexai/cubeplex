'use client'

import { useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import { createApiClient, createScheduledTask, patchScheduledTask } from '@cubebox/core'
import type { ScheduledTaskCreate, ScheduledTaskOut, ScheduledTaskPatch } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

interface ScheduledTaskFormDialogProps {
  wsId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  task: ScheduledTaskOut | null
  onSuccess: (task: ScheduledTaskOut) => void
}

function localDatetimeToIso(value: string): string {
  if (!value) return ''
  // datetime-local gives us "2030-01-01T09:00" — interpret as local time
  const d = new Date(value)
  if (isNaN(d.getTime())) return value
  return d.toISOString()
}

function isoToLocalDatetime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  // datetime-local format: "YYYY-MM-DDTHH:mm"
  const pad = (n: number): string => n.toString().padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}

export function ScheduledTaskFormDialog({
  wsId,
  open,
  onOpenChange,
  task,
  onSuccess,
}: ScheduledTaskFormDialogProps) {
  const isEdit = task !== null

  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [scheduleKind, setScheduleKind] = useState<'cron' | 'interval' | 'once'>('interval')
  const [cronExpr, setCronExpr] = useState('')
  const [intervalSeconds, setIntervalSeconds] = useState('3600')
  const [runAt, setRunAt] = useState('')
  const [timezone, setTimezone] = useState('UTC')
  const [targetMode, setTargetMode] = useState<'new_each_run' | 'fixed'>('new_each_run')
  const [targetConversationId, setTargetConversationId] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset form when dialog opens
  const [prevOpen, setPrevOpen] = useState(open)
  if (prevOpen !== open) {
    setPrevOpen(open)
    if (open) {
      if (task) {
        setName(task.name)
        setPrompt(task.prompt)
        setScheduleKind(task.schedule_kind)
        setCronExpr(task.cron_expr ?? '')
        setIntervalSeconds(task.interval_seconds != null ? String(task.interval_seconds) : '3600')
        setRunAt(isoToLocalDatetime(task.run_at))
        setTimezone(task.timezone)
        setTargetMode(task.target_mode)
        setTargetConversationId(task.target_conversation_id ?? '')
      } else {
        setName('')
        setPrompt('')
        setScheduleKind('interval')
        setCronExpr('')
        setIntervalSeconds('3600')
        setRunAt('')
        setTimezone('UTC')
        setTargetMode('new_each_run')
        setTargetConversationId('')
      }
      setError(null)
    }
  }

  async function handleSubmit(e: React.FormEvent): Promise<void> {
    e.preventDefault()
    setSaving(true)
    setError(null)

    const client = createApiClient('')
    client.setWorkspaceId(wsId)

    const body: ScheduledTaskCreate = {
      name: name.trim(),
      prompt: prompt.trim(),
      schedule_kind: scheduleKind,
      target_mode: targetMode,
    }

    if (scheduleKind === 'cron') {
      body.cron_expr = cronExpr.trim()
    } else if (scheduleKind === 'interval') {
      body.interval_seconds = Number(intervalSeconds)
    } else if (scheduleKind === 'once') {
      body.run_at = localDatetimeToIso(runAt)
    }

    if (timezone.trim() && timezone.trim() !== 'UTC') {
      body.timezone = timezone.trim()
    }

    if (targetMode === 'fixed' && targetConversationId.trim()) {
      body.target_conversation_id = targetConversationId.trim()
    }

    try {
      let result: ScheduledTaskOut
      if (isEdit && task) {
        const patch: ScheduledTaskPatch = { ...body }
        result = await patchScheduledTask(client, task.id, patch)
      } else {
        result = await createScheduledTask(client, body)
      }
      onSuccess(result)
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred')
    } finally {
      setSaving(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200" />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(560px,calc(100vw-32px))]',
            '-translate-x-1/2 -translate-y-1/2 max-h-[90vh] overflow-y-auto',
            'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200',
          )}
          data-testid="task-form-dialog"
        >
          <div className="flex items-start justify-between gap-3">
            <DialogPrimitive.Title className="text-base font-semibold">
              {isEdit ? 'Edit scheduled task' : 'New scheduled task'}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label="close"
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <X className="size-4" />
                </button>
              }
            />
          </div>

          <form onSubmit={(e) => void handleSubmit(e)} className="mt-4 flex flex-col gap-3">
            {/* Name */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="task-name">Name</Label>
              <Input
                id="task-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Daily digest"
                required
                maxLength={255}
              />
            </div>

            {/* Prompt */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="task-prompt">Prompt</Label>
              <Textarea
                id="task-prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Summarize today's news and send me a digest…"
                required
                rows={3}
                className="resize-y"
              />
            </div>

            {/* Schedule kind */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="task-schedule-kind">Schedule type</Label>
              <Select
                value={scheduleKind}
                onValueChange={(v) => setScheduleKind(v as typeof scheduleKind)}
              >
                <SelectTrigger id="task-schedule-kind">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="cron">Cron expression</SelectItem>
                  <SelectItem value="interval">Interval (repeating)</SelectItem>
                  <SelectItem value="once">Once at a specific time</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Conditional schedule fields */}
            {scheduleKind === 'cron' && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="task-cron">Cron expression</Label>
                <Input
                  id="task-cron"
                  value={cronExpr}
                  onChange={(e) => setCronExpr(e.target.value)}
                  placeholder="0 9 * * 1-5"
                  required
                  className="font-mono text-sm"
                />
                <p className="text-xs text-muted-foreground">
                  Standard 5-field cron (minute hour day-of-month month day-of-week)
                </p>
              </div>
            )}

            {scheduleKind === 'interval' && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="task-interval">Interval (seconds)</Label>
                <Input
                  id="task-interval"
                  type="number"
                  min={60}
                  step={1}
                  value={intervalSeconds}
                  onChange={(e) => setIntervalSeconds(e.target.value)}
                  placeholder="3600"
                  required
                />
                <p className="text-xs text-muted-foreground">Minimum 60 seconds</p>
              </div>
            )}

            {scheduleKind === 'once' && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="task-run-at">Run at</Label>
                <Input
                  id="task-run-at"
                  type="datetime-local"
                  value={runAt}
                  onChange={(e) => setRunAt(e.target.value)}
                  required
                />
              </div>
            )}

            {/* Timezone */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="task-timezone">Timezone</Label>
              <Input
                id="task-timezone"
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                placeholder="UTC"
              />
              <p className="text-xs text-muted-foreground">
                IANA timezone name (e.g. America/New_York, Asia/Shanghai)
              </p>
            </div>

            {/* Target mode */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="task-target-mode">Conversation target</Label>
              <Select
                value={targetMode}
                onValueChange={(v) => setTargetMode(v as typeof targetMode)}
              >
                <SelectTrigger id="task-target-mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="new_each_run">New conversation each run</SelectItem>
                  <SelectItem value="fixed">Fixed conversation</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {targetMode === 'fixed' && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="task-conversation-id">Conversation ID</Label>
                <Input
                  id="task-conversation-id"
                  value={targetConversationId}
                  onChange={(e) => setTargetConversationId(e.target.value)}
                  placeholder="conv_…"
                  required={targetMode === 'fixed'}
                />
                <p className="text-xs text-muted-foreground">
                  Must be one of your own conversations
                </p>
              </div>
            )}

            {error && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
                {error}
              </div>
            )}

            <div className="mt-1 flex items-center justify-end gap-2">
              <DialogPrimitive.Close
                render={
                  <Button type="button" variant="ghost" size="sm" disabled={saving}>
                    Cancel
                  </Button>
                }
              />
              <Button type="submit" size="sm" disabled={saving || !name.trim() || !prompt.trim()}>
                {saving
                  ? isEdit
                    ? 'Saving…'
                    : 'Creating…'
                  : isEdit
                    ? 'Save changes'
                    : 'Create task'}
              </Button>
            </div>
          </form>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
