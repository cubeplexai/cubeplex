'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import type { Provider, ProviderCreate, ProviderUpdate } from '@cubebox/core'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { cn } from '@/lib/utils'

type AuthType = 'api_key' | 'oauth' | 'none'

const PROVIDER_TYPES = ['openai_compat', 'openai', 'anthropic'] as const

interface ProviderFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  provider: Provider | null
  onSave: (body: ProviderCreate | ProviderUpdate) => Promise<void>
}

export function ProviderFormDialog({
  open,
  onOpenChange,
  provider,
  onSave,
}: ProviderFormDialogProps) {
  const t = useTranslations('adminModels')
  const isEdit = provider !== null
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [providerType, setProviderType] = useState('openai_compat')
  const [baseUrl, setBaseUrl] = useState('')
  const [authType, setAuthType] = useState<AuthType>('api_key')
  const [apiKey, setApiKey] = useState('')
  const [logoUrl, setLogoUrl] = useState('')
  const [extraHeaders, setExtraHeaders] = useState('')

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    if (open) {
      if (provider) {
        setName(provider.name)
        setProviderType(provider.provider_type)
        setBaseUrl(provider.base_url)
        // Legacy: bearer_token was a synonym for api_key (same wire format).
        const auth = provider.auth_type === 'bearer_token' ? 'api_key' : provider.auth_type
        setAuthType(auth as AuthType)
        setApiKey('')
        setLogoUrl(provider.logo_url ?? '')
        setExtraHeaders(
          provider.extra_headers ? JSON.stringify(provider.extra_headers, null, 2) : '',
        )
      } else {
        setName('')
        setProviderType('openai_compat')
        setBaseUrl('')
        setAuthType('api_key')
        setApiKey('')
        setLogoUrl('')
        setExtraHeaders('')
      }
      setError(null)
      setSaving(false)
    }
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [open, provider])

  const AUTH_OPTIONS: { value: AuthType; label: string; disabled?: boolean }[] = [
    { value: 'api_key', label: t('authApiKey') },
    { value: 'oauth', label: t('authOAuth'), disabled: true },
    { value: 'none', label: t('authNone') },
  ]

  async function handleSave(): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      let parsedHeaders: Record<string, unknown> | undefined
      if (extraHeaders.trim()) {
        try {
          parsedHeaders = JSON.parse(extraHeaders) as Record<string, unknown>
        } catch {
          setError(t('extraHeadersInvalid'))
          setSaving(false)
          return
        }
      }

      if (isEdit) {
        const body: ProviderUpdate = {
          name: name || null,
          provider_type: providerType || null,
          base_url: baseUrl || null,
          auth_type: authType,
          api_key: apiKey || null,
          logo_url: logoUrl || null,
          extra_headers: parsedHeaders ?? null,
        }
        await onSave(body)
      } else {
        const body: ProviderCreate = {
          name,
          provider_type: providerType,
          base_url: baseUrl,
          auth_type: authType,
          api_key: apiKey || null,
          logo_url: logoUrl || null,
          extra_headers: parsedHeaders,
        }
        await onSave(body)
      }
      onOpenChange(false)
    } catch (e) {
      setError((e as Error).message)
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
            'fixed left-1/2 top-1/2 z-50 w-[min(520px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
            'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200',
          )}
          data-testid="provider-form-dialog"
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold">
                {isEdit ? t('editTitle') : t('createTitle')}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="mt-0.5 text-xs text-muted-foreground">
                {isEdit ? t('editDesc') : t('createDesc')}
              </DialogPrimitive.Description>
            </div>
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

          <div className="mt-4 flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="provider-name">{t('name')}</Label>
              <Input
                id="provider-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="OpenAI"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="provider-type">{t('providerType')}</Label>
              <select
                id="provider-type"
                value={providerType}
                onChange={(e) => setProviderType(e.target.value)}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {PROVIDER_TYPES.map((pt) => (
                  <option key={pt} value={pt}>
                    {pt}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="provider-base-url">{t('baseUrl')}</Label>
              <Input
                id="provider-base-url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.openai.com/v1"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>{t('authType')}</Label>
              <RadioGroup
                value={authType}
                onValueChange={(v) => setAuthType(v as AuthType)}
                className="grid grid-cols-2 gap-2"
              >
                {AUTH_OPTIONS.map((opt) => (
                  <label
                    key={opt.value}
                    className={cn(
                      'flex items-center gap-2 rounded-lg border px-3 py-2.5 text-sm transition-colors',
                      authType === opt.value
                        ? 'border-primary/40 bg-primary/5'
                        : 'border-border/70 hover:border-border',
                      opt.disabled ? 'cursor-not-allowed bg-muted/20 opacity-50' : 'cursor-pointer',
                    )}
                  >
                    <RadioGroupItem value={opt.value} disabled={opt.disabled} />
                    <span className="flex-1 text-sm">{opt.label}</span>
                    {opt.disabled && (
                      <span className="text-[10px] text-muted-foreground/70">
                        {t('comingSoon')}
                      </span>
                    )}
                  </label>
                ))}
              </RadioGroup>
            </div>

            {authType === 'api_key' && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="provider-api-key">{t('apiKey')}</Label>
                <Input
                  id="provider-api-key"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={isEdit ? t('apiKeyEditHint') : ''}
                />
                {isEdit && (
                  <span className="text-[11px] text-muted-foreground">{t('apiKeyEditHint')}</span>
                )}
              </div>
            )}

            <Accordion className="mt-1">
              <AccordionItem value="logo">
                <AccordionTrigger className="text-xs text-muted-foreground">
                  {t('logoUrl')}
                </AccordionTrigger>
                <AccordionContent>
                  <Input
                    value={logoUrl}
                    onChange={(e) => setLogoUrl(e.target.value)}
                    placeholder="https://example.com/logo.png"
                  />
                </AccordionContent>
              </AccordionItem>
              <AccordionItem value="headers">
                <AccordionTrigger className="text-xs text-muted-foreground">
                  {t('extraHeaders')}
                </AccordionTrigger>
                <AccordionContent>
                  <textarea
                    value={extraHeaders}
                    onChange={(e) => setExtraHeaders(e.target.value)}
                    placeholder='{"X-Custom-Header": "value"}'
                    rows={3}
                    className="h-20 w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                  />
                </AccordionContent>
              </AccordionItem>
            </Accordion>

            {error && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
                {error}
              </div>
            )}
          </div>

          <div className="mt-4 flex items-center justify-end gap-2">
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
              onClick={() => void handleSave()}
              disabled={saving || !name}
            >
              {saving ? t('saving') : t('save')}
            </Button>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
