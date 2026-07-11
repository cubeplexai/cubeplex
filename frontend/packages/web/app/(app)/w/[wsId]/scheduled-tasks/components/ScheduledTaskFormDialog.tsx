'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Info, X } from 'lucide-react'
import {
  createApiClient,
  createScheduledTask,
  patchScheduledTask,
  retargetScheduledTaskDestination,
} from '@cubebox/core'
import type {
  ScheduledTaskCreate,
  ScheduledTaskOut,
  ScheduledTaskPatch,
  ScheduledTaskRetarget,
  TargetMode,
} from '@cubebox/core'
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

/** Modes the create form can send; im_channel is edit/retarget only from web. */
type CreateTargetMode = 'fixed' | 'new_each_run'

function destinationChanged(
  task: ScheduledTaskOut,
  mode: TargetMode,
  convId: string,
  topicId: string | null,
): boolean {
  if (task.target_mode !== mode) return true
  if (mode === 'fixed') return (task.target_conversation_id ?? '') !== convId.trim()
  if (mode === 'new_each_run') return (task.topic_id ?? null) !== topicId
  return false
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
  const [targetMode, setTargetMode] = useState<TargetMode>('new_each_run')
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
        setTargetMode(task.target_mode)
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
        // Destination first: im_channel retarget is the failure-prone call (422
        // when no IMThreadLink). Doing it before PATCH avoids persisting
        // prompt/schedule changes while reporting a failed save.
        if (destinationChanged(task, targetMode, targetConversationId, topicId)) {
          const body: ScheduledTaskRetarget = { target_mode: targetMode }
          if (targetMode === 'fixed') {
            body.target_conversation_id = targetConversationId.trim()
          } else if (targetMode === 'new_each_run') {
            body.topic_id = topicId
          } else {
            // im_channel: resolve from fixed conv field if filled, else server
            // falls back to the task's current fixed conversation / topic.
            if (targetConversationId.trim()) {
              body.anchor_conversation_id = targetConversationId.trim()
            } else if (task.target_conversation_id) {
              body.anchor_conversation_id = task.target_conversation_id
            }
            if (topicId) {
              body.topic_id = topicId
            } else if (task.topic_id) {
              body.topic_id = task.topic_id
            }
          }
          result = await retargetScheduledTaskDestination(client, task.id, body)
        } else {
          result = task
        }

        const patch: ScheduledTaskPatch = {
          name: name.trim(),
          prompt: prompt.trim(),
          ...scheduleFields,
        }
        // topic_id still patchable when staying on new_each_run (legacy path);
        // full mode switches already applied via retarget above.
        if (task.target_mode === 'new_each_run' && targetMode === 'new_each_run') {
          patch.topic_id = topicId
        }
        result = await patchScheduledTask(client, task.id, patch)
      } else {
        if (targetMode === 'im_channel') {
          setError(t('targetImChannelCreateOnlyFromIm'))
          setSaving(false)
          return
        }
        const createMode = targetMode as CreateTargetMode
        const body: ScheduledTaskCreate = {
          name: name.trim(),
          prompt: prompt.trim(),
          target_mode: createMode,
          ...scheduleFields,
        }
        if (createMode === 'fixed' && targetConversationId.trim()) {
          body.target_conversation_id = targetConversationId.trim()
        }
        if (createMode === 'new_each_run') {
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

  const imCreateDisabled = !isEdit

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

            <div className="flex flex-col gap-1.5">
              <Label>{t('schedule')}</Label>
              <ScheduleEditor value={scheduleValue} onChange={setScheduleValue} />
            </div>

            <TooltipProvider>
              <div className="flex flex-col gap-1.5">
                <Label id="task-target-mode-label">{t('target')}</Label>
                <RadioGroup
                  value={targetMode}
                  onValueChange={(v) => {
                    if (v === 'fixed' || v === 'new_each_run' || v === 'im_channel') {
                      if (v === 'im_channel' && imCreateDisabled) return
                      setTargetMode(v)
                    }
                  }}
                  aria-labelledby="task-target-mode-label"
                  className="gap-1.5"
                >
                  <DestinationOption
                    value="new_each_run"
                    checked={targetMode === 'new_each_run'}
                    title={t('targetNewEachRun')}
                    hint={t('targetNewEachRunHint')}
                  />
                  <DestinationOption
                    value="fixed"
                    checked={targetMode === 'fixed'}
                    title={t('targetFixed')}
                    hint={t('targetFixedHint')}
                  />
                  {imCreateDisabled ? (
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
                  ) : (
                    <DestinationOption
                      value="im_channel"
                      checked={targetMode === 'im_channel'}
                      title={t('targetImChannel')}
                      hint={t('targetImChannelRetargetHint')}
                    />
                  )}
                </RadioGroup>
                {isEdit && targetMode === 'im_channel' && (
                  <p className="text-[11px] text-muted-foreground">
                    {t('targetImChannelRetargetNote')}
                  </p>
                )}
              </div>

              {targetMode === 'new_each_run' && (
                <div className="flex flex-col gap-1.5">
                  <Label id="task-topic-label">{t('targetTopic')}</Label>
                  <TopicPicker
                    wsId={wsId}
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
                    required
                    disabled={saving}
                  />
                  <p className="text-xs text-muted-foreground">{t('conversationIdHint')}</p>
                </div>
              )}

              {targetMode === 'im_channel' && isEdit && task?.im_channel_id && (
                <ReadOnlyImChannelDestination
                  wsId={wsId}
                  accountId={task.im_account_id ?? ''}
                  channelId={task.im_channel_id ?? ''}
                  scopeKey={task.im_scope_key ?? ''}
                  scopeKind={task.im_scope_kind}
                />
              )}
            </TooltipProvider>

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
