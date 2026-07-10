'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Info, X } from 'lucide-react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { createApiClient, useMemberStore, type CreateTriggerBody } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { TriggerTopicPicker } from './TopicPicker'

interface TriggerFormProps {
  wsId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (triggerId: string, webhookSecret: string) => void
  onSubmit: (body: CreateTriggerBody) => Promise<{ id: string }>
}

export function TriggerForm({ wsId, open, onOpenChange, onCreated, onSubmit }: TriggerFormProps) {
  const t = useTranslations('triggers')
  const client = useMemo(() => createApiClient(''), [])
  const { wsMembers, loadWsMembers } = useMemberStore()

  const [name, setName] = useState('')
  const [webhookSecret, setWebhookSecret] = useState('')
  const [promptTemplate, setPromptTemplate] = useState('')
  const [payloadFields, setPayloadFields] = useState('')
  const [runAsUserId, setRunAsUserId] = useState('')
  const [rateLimitResponse, setRateLimitResponse] = useState<'429' | '202_drop'>('429')
  const [topicId, setTopicId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      void loadWsMembers(client, wsId)
      /* eslint-disable react-hooks/set-state-in-effect */
      setName('')
      setWebhookSecret('')
      setPromptTemplate('')
      setPayloadFields('')
      setRunAsUserId('')
      setRateLimitResponse('429')
      setTopicId(null)
      setError(null)
      setSaving(false)
      /* eslint-enable react-hooks/set-state-in-effect */
    }
  }, [open, client, wsId, loadWsMembers])

  useEffect(() => {
    if (wsMembers.length > 0 && !runAsUserId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setRunAsUserId(wsMembers[0].user_id)
    }
  }, [wsMembers, runAsUserId])

  const handleSubmit = useCallback(async () => {
    setSaving(true)
    setError(null)
    try {
      const fields = payloadFields
        .split(',')
        .map((f) => f.trim())
        .filter(Boolean)

      const body: CreateTriggerBody = {
        name: name.trim(),
        webhook_secret: webhookSecret,
        prompt_template: promptTemplate,
        payload_fields: fields,
        run_as_user_id: runAsUserId,
        rate_limit_response: rateLimitResponse,
        conversation_policy: 'new_each_time',
        topic_id: topicId,
        target_type: 'inline',
        source_type: 'webhook',
      }

      const trigger = await onSubmit(body)
      onCreated(trigger.id, webhookSecret)
      onOpenChange(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }, [
    name,
    webhookSecret,
    promptTemplate,
    payloadFields,
    runAsUserId,
    rateLimitResponse,
    topicId,
    onSubmit,
    onCreated,
    onOpenChange,
  ])

  const canSubmit = name.trim() && webhookSecret && promptTemplate && runAsUserId

  return (
    <DialogPrimitive.Root open={open} disablePointerDismissal onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop
          className={cn(
            'fixed inset-0 z-50 bg-black/40 backdrop-blur-sm',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0',
            'transition-opacity duration-200',
          )}
        />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50',
            'w-[min(560px,calc(100vw-32px))]',
            '-translate-x-1/2 -translate-y-1/2',
            'max-h-[90vh] overflow-y-auto',
            'rounded-xl border border-border bg-popover p-5',
            'text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0',
            'transition-opacity duration-200',
          )}
          data-testid="create-trigger-dialog"
        >
          <div className="flex items-start justify-between gap-3">
            <DialogPrimitive.Title className="text-base font-semibold">
              {t('createTrigger')}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label="close"
                  className={cn(
                    'rounded-md p-1 text-muted-foreground',
                    'hover:bg-muted hover:text-foreground',
                  )}
                >
                  <X className="size-4" />
                </button>
              }
            />
          </div>

          <div className="mt-4 flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="trigger-name">{t('fieldName')}</Label>
              <Input
                id="trigger-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('fieldNamePlaceholder')}
                data-testid="trigger-name-input"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="trigger-secret">{t('fieldWebhookSecret')}</Label>
              <Input
                id="trigger-secret"
                type="password"
                name="trigger-webhook-secret"
                value={webhookSecret}
                onChange={(e) => setWebhookSecret(e.target.value)}
                placeholder={t('fieldWebhookSecretPlaceholder')}
                autoComplete="new-password"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                data-testid="trigger-secret-input"
              />
              <p className="text-xs text-muted-foreground">{t('fieldWebhookSecretHint')}</p>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="trigger-template">{t('fieldPromptTemplate')}</Label>
              <Textarea
                id="trigger-template"
                value={promptTemplate}
                onChange={(e) => setPromptTemplate(e.target.value)}
                placeholder={t('fieldPromptTemplatePlaceholder')}
                rows={4}
                data-testid="trigger-template-input"
              />
              <p className="text-xs text-muted-foreground">{t('fieldPromptTemplateHint')}</p>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="trigger-payload-fields">{t('fieldPayloadFields')}</Label>
              <Input
                id="trigger-payload-fields"
                value={payloadFields}
                onChange={(e) => setPayloadFields(e.target.value)}
                placeholder={t('fieldPayloadFieldsPlaceholder')}
                data-testid="trigger-payload-fields-input"
              />
              <p className="text-xs text-muted-foreground">{t('fieldPayloadFieldsHint')}</p>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="trigger-run-as">{t('fieldRunAsUser')}</Label>
              <Select value={runAsUserId} onValueChange={(v) => setRunAsUserId(v ?? '')}>
                <SelectTrigger id="trigger-run-as" data-testid="trigger-run-as-select">
                  <SelectValue placeholder={t('fieldRunAsUserPlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  {wsMembers.map((m) => (
                    <SelectItem key={m.user_id} value={m.user_id}>
                      {m.email}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Destination */}
            <TooltipProvider>
              <div className="flex flex-col gap-1.5">
                <Label id="trigger-destination-label">{t('fieldDestination')}</Label>
                <RadioGroup
                  value="new_each_time"
                  onValueChange={() => undefined}
                  aria-labelledby="trigger-destination-label"
                  className="gap-1.5"
                >
                  <DestinationOption
                    value="new_each_time"
                    checked
                    title={t('destNewEachTime')}
                    hint={t('destNewEachTimeHint')}
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
                        title={t('destImChannel')}
                        hint={t('destImChannelHint')}
                        disabled
                        trailing={<Info className="size-3 text-muted-foreground" />}
                      />
                    </TooltipTrigger>
                    <TooltipContent>{t('destImChannelDisabledHint')}</TooltipContent>
                  </Tooltip>
                </RadioGroup>
              </div>

              <div className="flex flex-col gap-1.5">
                <Label id="trigger-topic-label">{t('destTopic')}</Label>
                <TriggerTopicPicker
                  wsId={wsId}
                  id="trigger-topic"
                  aria-labelledby="trigger-topic-label"
                  value={topicId}
                  onChange={setTopicId}
                  placeholder={t('destTopicEmpty')}
                  clearable
                  disabled={saving}
                />
                <p className="text-xs text-muted-foreground">{t('destTopicHint')}</p>
              </div>
            </TooltipProvider>

            <div className="flex flex-col gap-1.5">
              <Label>{t('fieldRateLimitResponse')}</Label>
              <div className="flex flex-col gap-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="rate-limit-response"
                    value="429"
                    checked={rateLimitResponse === '429'}
                    onChange={() => setRateLimitResponse('429')}
                    data-testid="rate-limit-429"
                  />
                  <span className="text-sm">{t('rateLimitResponse429')}</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="rate-limit-response"
                    value="202_drop"
                    checked={rateLimitResponse === '202_drop'}
                    onChange={() => setRateLimitResponse('202_drop')}
                    data-testid="rate-limit-202-drop"
                  />
                  <span className="text-sm">{t('rateLimitResponse202Drop')}</span>
                </label>
              </div>
            </div>

            {error && (
              <div
                className={cn(
                  'rounded-md border border-destructive/30',
                  'bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive',
                )}
              >
                {error}
              </div>
            )}
          </div>

          <div className="mt-5 flex items-center justify-end gap-2">
            <DialogPrimitive.Close
              render={
                <Button type="button" variant="ghost" size="sm" disabled={saving}>
                  {t('cancel')}
                </Button>
              }
            />
            <Button
              type="button"
              size="sm"
              onClick={() => void handleSubmit()}
              disabled={saving || !canSubmit}
              data-testid="create-trigger-submit"
            >
              {saving ? t('creating') : t('createTrigger')}
            </Button>
          </div>
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
      data-testid={`trigger-destination-option-${value}`}
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
