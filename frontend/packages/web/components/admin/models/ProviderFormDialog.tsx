'use client'

import { useState, useEffect } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import type { Provider, ProviderCreate, ProviderUpdate, ApiClient, TestResult } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from '@/components/ui/accordion'
import { cn } from '@/lib/utils'
import { TestConnectionResult } from './TestConnectionResult'

type AuthType = 'api_key' | 'bearer_token' | 'oauth' | 'none'

const AUTH_OPTIONS: { value: AuthType; label: string; disabled?: boolean; tooltip?: string }[] = [
  { value: 'api_key', label: 'API Key' },
  { value: 'bearer_token', label: 'Bearer Token' },
  { value: 'oauth', label: 'OAuth 2.0', disabled: true, tooltip: '即将推出' },
  { value: 'none', label: '无认证' },
]

interface ProviderFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  provider: Provider | null // null = create, non-null = edit
  client: ApiClient
  onTestConnection: (
    client: ApiClient,
    body: {
      provider_type: string
      base_url: string
      api_key?: string | null
      auth_type: string
    },
  ) => Promise<TestResult>
  onSave: (body: ProviderCreate | ProviderUpdate) => Promise<void>
}

export function ProviderFormDialog({
  open,
  onOpenChange,
  provider,
  client,
  onTestConnection,
  onSave,
}: ProviderFormDialogProps) {
  const isEdit = provider !== null
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Form fields
  const [name, setName] = useState('')
  const [providerType, setProviderType] = useState('openai')
  const [baseUrl, setBaseUrl] = useState('')
  const [authType, setAuthType] = useState<AuthType>('api_key')
  const [apiKey, setApiKey] = useState('')
  const [logoUrl, setLogoUrl] = useState('')
  const [extraHeaders, setExtraHeaders] = useState('')

  // Test connection
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    if (open) {
      if (provider) {
        setName(provider.name)
        setProviderType(provider.provider_type)
        setBaseUrl(provider.base_url)
        setAuthType(provider.auth_type as AuthType)
        setApiKey('')
        setLogoUrl(provider.logo_url ?? '')
        setExtraHeaders(
          provider.extra_headers ? JSON.stringify(provider.extra_headers, null, 2) : '',
        )
      } else {
        setName('')
        setProviderType('openai')
        setBaseUrl('')
        setAuthType('api_key')
        setApiKey('')
        setLogoUrl('')
        setExtraHeaders('')
      }
      setError(null)
      setTestResult(null)
      setSaving(false)
      setTesting(false)
    }
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [open, provider])

  function reset(): void {
    setName('')
    setProviderType('openai')
    setBaseUrl('')
    setAuthType('api_key')
    setApiKey('')
    setLogoUrl('')
    setExtraHeaders('')
    setError(null)
    setTestResult(null)
    setSaving(false)
    setTesting(false)
  }

  function handleOpenChange(next: boolean): void {
    if (!next) reset()
    onOpenChange(next)
  }

  function buildTestPayload() {
    return {
      provider_type: providerType,
      base_url: baseUrl,
      api_key: apiKey || null,
      auth_type: authType,
    }
  }

  async function handleTest(): Promise<void> {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await onTestConnection(client, buildTestPayload())
      setTestResult(result)
    } catch (e) {
      setTestResult({ ok: false, error: (e as Error).message, latency_ms: 0 })
    } finally {
      setTesting(false)
    }
  }

  async function handleSave(): Promise<void> {
    setSaving(true)
    setError(null)
    try {
      let parsedHeaders: Record<string, unknown> | undefined
      if (extraHeaders.trim()) {
        try {
          parsedHeaders = JSON.parse(extraHeaders) as Record<string, unknown>
        } catch {
          setError('extra_headers 格式无效，请输入合法 JSON')
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
      handleOpenChange(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
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
                {isEdit ? '编辑 Provider' : '添加 Provider'}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="mt-0.5 text-xs text-muted-foreground">
                {isEdit ? '修改 provider 配置' : '添加新的 LLM provider'}
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
            {/* Name */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="provider-name">名称</Label>
              <Input
                id="provider-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. OpenAI"
              />
            </div>

            {/* Provider Type */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="provider-type">Provider 类型</Label>
              <select
                id="provider-type"
                value={providerType}
                onChange={(e) => setProviderType(e.target.value)}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 py-1 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="google">Google</option>
                <option value="azure">Azure</option>
                <option value="aws_bedrock">AWS Bedrock</option>
                <option value="openrouter">OpenRouter</option>
                <option value="together">Together AI</option>
                <option value="ollama">Ollama</option>
                <option value="custom">Custom</option>
              </select>
            </div>

            {/* Base URL */}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="provider-base-url">Base URL</Label>
              <Input
                id="provider-base-url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.openai.com/v1"
              />
            </div>

            {/* Auth Type */}
            <div className="flex flex-col gap-1.5">
              <Label>认证方式</Label>
              <RadioGroup
                value={authType}
                onValueChange={(v) => setAuthType(v as AuthType)}
                className="grid grid-cols-2 gap-2"
              >
                {AUTH_OPTIONS.map((opt) => (
                  <label
                    key={opt.value}
                    className={cn(
                      'flex items-center gap-2 rounded-lg border px-3 py-2.5 text-sm cursor-pointer transition-colors',
                      authType === opt.value
                        ? 'border-primary/40 bg-primary/5'
                        : 'border-border/70 hover:border-border',
                      opt.disabled && 'opacity-50 cursor-not-allowed bg-muted/20',
                    )}
                  >
                    <RadioGroupItem value={opt.value} disabled={opt.disabled} />
                    <span className="text-sm">{opt.label}</span>
                  </label>
                ))}
              </RadioGroup>
            </div>

            {/* API Key (shown for api_key and bearer_token) */}
            {(authType === 'api_key' || authType === 'bearer_token') && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="provider-api-key">
                  {authType === 'bearer_token' ? 'Bearer Token' : 'API Key'}
                </Label>
                <Input
                  id="provider-api-key"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={isEdit ? '留空则不修改' : ''}
                />
                {isEdit && (
                  <span className="text-[11px] text-muted-foreground">
                    留空表示不修改已设置的密钥
                  </span>
                )}
              </div>
            )}

            {/* Advanced Settings */}
            <Accordion className="mt-1">
              <AccordionItem value="logo">
                <AccordionTrigger className="text-xs text-muted-foreground">
                  Logo URL
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
                  Extra Headers (JSON)
                </AccordionTrigger>
                <AccordionContent>
                  <textarea
                    value={extraHeaders}
                    onChange={(e) => setExtraHeaders(e.target.value)}
                    placeholder='{"X-Custom-Header": "value"}'
                    rows={3}
                    className="h-20 w-full rounded-lg border border-input bg-transparent px-2.5 py-1.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 placeholder:text-muted-foreground"
                  />
                </AccordionContent>
              </AccordionItem>
            </Accordion>

            {error && (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
                {error}
              </div>
            )}

            <TestConnectionResult result={testResult} busy={testing} />
          </div>

          <div className="mt-4 flex items-center justify-between gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handleTest()}
              disabled={testing || saving || !baseUrl}
            >
              测试连接
            </Button>
            <div className="flex items-center gap-2">
              <DialogPrimitive.Close
                render={
                  <Button type="button" variant="ghost" size="sm" disabled={saving}>
                    取消
                  </Button>
                }
              />
              <Button
                type="button"
                size="sm"
                onClick={() => void handleSave()}
                disabled={saving || !name}
              >
                {saving ? '保存中...' : '保存'}
              </Button>
            </div>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
