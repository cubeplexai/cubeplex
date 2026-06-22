'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Info, X } from 'lucide-react'
import { createApiClient, createScheduledTask, patchScheduledTask } from '@cubebox/core'
import type { ScheduledTaskCreate, ScheduledTaskOut, ScheduledTaskPatch } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import { ScheduleEditor } from './ScheduleEditor'
import { TopicPicker } from './TopicPicker'
import { ReadOnlyImChannelDestination } from './ReadOnlyImChannelDestination'
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

/** Editable destination modes (im_channel is created only by IM ingest). */
type EditableTargetMode = 'fixed' | 'new_each_run'

export function ScheduledTaskFormDialog({
  wsId,
  open,
  onOpenChange,
  task,
  onSuccess,
}: ScheduledTaskFormDialogProps) {
  const t = useTranslations('scheduledTasks')
  const isEdit = task !== null
  // im_channel rows are created by IM ingest; user can't switch destination
  // after creation. The form keeps prompt/schedule editable but locks the
  // destination block.
  const isReadOnlyDestination = task?.target_mode === 'im_channel'

  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [scheduleValue, setScheduleValue] = useState<ScheduleEditorValue>(
    defaultScheduleEditorValue(detectTimezone()),
  )
  const [targetMode, setTargetMode] = useState<EditableTargetMode>('new_each_run')
  const [targetConversationId, setTargetConversationId] = useState('')
  const [topicId, setTopicId] = useState<string | null>(null)
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
        // im_channel rows keep their destination as-is; the editor radio is
        // disabled below. Use new_each_run as the inert UI default in case the
        // user toggles their browser dev tools to bypass the disabled state.
        setTargetMode(task.target_mode === 'im_channel' ? 'new_each_run' : task.target_mode)
        setTargetConversationId(task.target_conversation_id ?? '')
        setTopicId(task.topic_id)
      } else {
        setName('')
        setPrompt('')
        setScheduleValue(defaultScheduleEditorValue(detectTimezone()))
        setTargetMode('new_each_run')
        setTargetConversationId('')
        setTopicId(null)
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

    try {
      let result: ScheduledTaskOut
      if (isEdit && task) {
        // PATCH never sends destination fields. The backend rejects
        // target_mode / im_* mutations with 422; topic_id is the only mutable
        // destination knob, and only when the existing mode is new_each_run.
        const patch: ScheduledTaskPatch = {
          name: name.trim(),
          prompt: prompt.trim(),
          ...scheduleFields,
        }
        if (!isReadOnlyDestination && task.target_mode === 'new_each_run') {
          patch.topic_id = topicId
        }
        result = await patchScheduledTask(client, task.id, patch)
      } else {
        const body: ScheduledTaskCreate = {
          name: name.trim(),
          prompt: prompt.trim(),
          target_mode: targetMode,
          ...scheduleFields,
        }
        if (targetMode === 'fixed' && targetConversationId.trim()) {
          body.target_conversation_id = targetConversationId.trim()
        }
        if (targetMode === 'new_each_run') {
          body.topic_id = topicId
        }
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

            {/* Destination */}
            {isReadOnlyDestination && task ? (
              <div className="flex flex-col gap-1.5">
                <Label>{t('target')}</Label>
                <ReadOnlyImChannelDestination
                  wsId={wsId}
                  accountId={task.im_account_id ?? ''}
                  channelId={task.im_channel_id ?? ''}
                  scopeKey={task.im_scope_key ?? ''}
                  scopeKind={task.im_scope_kind}
                />
              </div>
            ) : (
              <TooltipProvider>
                <div className="flex flex-col gap-1.5">
                  <Label id="task-target-mode-label">{t('target')}</Label>
                  <RadioGroup
                    value={targetMode}
                    onValueChange={(v) => {
                      if (v === 'fixed' || v === 'new_each_run') setTargetMode(v)
                    }}
                    disabled={isEdit}
                    aria-labelledby="task-target-mode-label"
                    className="gap-1.5"
                  >
                    <DestinationOption
                      value="new_each_run"
                      checked={targetMode === 'new_each_run'}
                      title={t('targetNewEachRun')}
                      hint={t('targetNewEachRunHint')}
                      disabled={isEdit && task?.target_mode !== 'new_each_run'}
                    />
                    <DestinationOption
                      value="fixed"
                      checked={targetMode === 'fixed'}
                      title={t('targetFixed')}
                      hint={t('targetFixedHint')}
                      disabled={isEdit && task?.target_mode !== 'fixed'}
                    />
                    <Tooltip>
                      <TooltipTrigger
                        type="button"
                        disabled
                        aria-disabled
                        className="block w-full text-left"
                      >
                        <DestinationOption
                          value="im_channel"
                          checked={false}
                          title={t('targetImChannel')}
                          hint={t('targetImChannelHint')}
                          disabled
                          trailing={<Info className="size-3 text-muted-foreground" />}
                        />
                      </TooltipTrigger>
                      <TooltipContent>{t('targetImChannelDisabledHint')}</TooltipContent>
                    </Tooltip>
                  </RadioGroup>
                  {isEdit && (
                    <p className="text-[11px] italic text-muted-foreground">
                      {t('targetLockedAfterCreate')}
                    </p>
                  )}
                </div>

                {targetMode === 'new_each_run' && (
                  <div className="flex flex-col gap-1.5">
                    <Label id="task-topic-label">{t('targetTopic')}</Label>
                    <TopicPicker
                      id="task-topic"
                      aria-labelledby="task-topic-label"
                      value={topicId}
                      onChange={setTopicId}
                      placeholder={t('targetTopicEmpty')}
                      clearable
                      disabled={saving}
                    />
                    <p className="text-xs text-muted-foreground">{t('targetTopicHint')}</p>
                  </div>
                )}

                {targetMode === 'fixed' && (
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="task-conversation-id">{t('conversationId')}</Label>
                    <Input
                      id="task-conversation-id"
                      value={targetConversationId}
                      onChange={(e) => setTargetConversationId(e.target.value)}
                      placeholder="conv_…"
                      required={targetMode === 'fixed'}
                      disabled={isEdit}
                    />
                    <p className="text-xs text-muted-foreground">{t('conversationIdHint')}</p>
                  </div>
                )}
              </TooltipProvider>
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

interface DestinationOptionProps {
  value: string
  checked: boolean
  title: string
  hint: string
  disabled?: boolean
  trailing?: React.ReactNode
}

function DestinationOption({
  value,
  checked,
  title,
  hint,
  disabled,
  trailing,
}: DestinationOptionProps) {
  return (
    <label
      className={cn(
        'flex items-start gap-2 rounded-md border px-3 py-2 transition-colors',
        checked && !disabled ? 'border-primary/60 bg-primary/5' : 'border-border bg-transparent',
        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:border-primary/40',
      )}
      data-testid={`destination-option-${value}`}
    >
      <RadioGroupItem value={value} disabled={disabled} className="mt-0.5" />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
          {title}
          {trailing}
        </span>
        <span className="text-[11px] text-muted-foreground">{hint}</span>
      </span>
    </label>
  )
}
