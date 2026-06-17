'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
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
import { ScheduleEditor } from './ScheduleEditor'
import {
  buildSchedulePayload,
  defaultScheduleEditorValue,
  parseSchedulePayload,
  type ScheduleEditorValue,
} from '../lib/schedulePayload'

interface ScheduledTaskFormDialogProps {
  wsId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  task: ScheduledTaskOut | null
  onSuccess: (task: ScheduledTaskOut) => void
}

function detectTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    return 'UTC'
  }
}

export function ScheduledTaskFormDialog({
  wsId,
  open,
  onOpenChange,
  task,
  onSuccess,
}: ScheduledTaskFormDialogProps) {
  const t = useTranslations('scheduledTasks')
  const isEdit = task !== null

  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [scheduleValue, setScheduleValue] = useState<ScheduleEditorValue>(
    defaultScheduleEditorValue(detectTimezone()),
  )
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
        setScheduleValue(parseSchedulePayload(task))
        setTargetMode(task.target_mode)
        setTargetConversationId(task.target_conversation_id ?? '')
      } else {
        setName('')
        setPrompt('')
        setScheduleValue(defaultScheduleEditorValue(detectTimezone()))
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

    const scheduleFields = buildSchedulePayload(scheduleValue)
    const body: ScheduledTaskCreate = {
      name: name.trim(),
      prompt: prompt.trim(),
      target_mode: targetMode,
      ...scheduleFields,
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
    <DialogPrimitive.Root open={open} disablePointerDismissal onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200" />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(580px,calc(100vw-32px))]',
            '-translate-x-1/2 -translate-y-1/2 max-h-[90vh] overflow-y-auto',
            'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200',
          )}
          data-testid="task-form-dialog"
        >
          <div className="flex items-start justify-between gap-3">
            <DialogPrimitive.Title className="text-base font-semibold">
              {isEdit ? t('dialogEditTitle') : t('dialogNewTitle')}
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
              <Label htmlFor="task-name">{t('name')}</Label>
              <Input
                id="task-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('namePlaceholder')}
                required
                maxLength={255}
              />
            </div>

            {/* Prompt */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="task-prompt">{t('prompt')}</Label>
              <Textarea
                id="task-prompt"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder={t('promptPlaceholder')}
                required
                rows={3}
                className="resize-y"
              />
            </div>

            {/* Schedule */}
            <div className="flex flex-col gap-1.5">
              <Label>{t('schedule')}</Label>
              <ScheduleEditor value={scheduleValue} onChange={setScheduleValue} />
            </div>

            {/* Target mode */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="task-target-mode">{t('target')}</Label>
              <Select
                value={targetMode}
                items={[
                  { value: 'new_each_run', label: t('targetNewEachRun') },
                  { value: 'fixed', label: t('targetFixed') },
                ]}
                onValueChange={(v) => setTargetMode(v as typeof targetMode)}
              >
                <SelectTrigger id="task-target-mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="new_each_run">{t('targetNewEachRun')}</SelectItem>
                  <SelectItem value="fixed">{t('targetFixed')}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {targetMode === 'fixed' && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="task-conversation-id">{t('conversationId')}</Label>
                <Input
                  id="task-conversation-id"
                  value={targetConversationId}
                  onChange={(e) => setTargetConversationId(e.target.value)}
                  placeholder="conv_…"
                  required={targetMode === 'fixed'}
                />
                <p className="text-xs text-muted-foreground">{t('conversationIdHint')}</p>
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
                    {t('cancel')}
                  </Button>
                }
              />
              <Button type="submit" size="sm" disabled={saving || !name.trim() || !prompt.trim()}>
                {saving
                  ? isEdit
                    ? t('saving')
                    : t('creating')
                  : isEdit
                    ? t('saveChanges')
                    : t('createTask')}
              </Button>
            </div>
          </form>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
