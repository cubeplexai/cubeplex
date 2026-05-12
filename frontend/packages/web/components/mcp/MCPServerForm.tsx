'use client'

import { useState } from 'react'
import type { FormEvent } from 'react'
import { Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type {
  MCPAuthMethod,
  MCPCredentialScope,
  MCPTestConnectionResult,
  MCPTransport,
} from '@cubebox/core'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import { MCPSecretInput } from './MCPSecretInput'

export type MCPServerFormMode = 'admin' | 'ws-member'

export interface MCPServerFormValues {
  name: string
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  credential_scope: MCPCredentialScope
  credential_plaintext: string
  credential_name: string
  headers: Record<string, string>
  timeout: number
  sse_read_timeout: number
}

export interface MCPServerFormProps {
  mode: MCPServerFormMode
  initial?: Partial<MCPServerFormValues>
  onSubmit: (values: MCPServerFormValues) => Promise<void>
  onTestConnection: (values: MCPServerFormValues) => Promise<MCPTestConnectionResult>
  onCancel: () => void
}

const DEFAULT_VALUES: MCPServerFormValues = {
  name: '',
  server_url: '',
  transport: 'streamable_http',
  auth_method: 'static',
  credential_scope: 'workspace',
  credential_plaintext: '',
  credential_name: '',
  headers: {},
  timeout: 30,
  sse_read_timeout: 300,
}

const adminScopes: MCPCredentialScope[] = ['org', 'user', 'none']
const workspaceScopes: MCPCredentialScope[] = ['workspace', 'user', 'none']

const scopeKey = {
  org: { title: 'orgTitle', help: 'orgHelp' },
  workspace: { title: 'workspaceTitle', help: 'workspaceHelp' },
  user: { title: 'userTitle', help: 'userHelp' },
  none: { title: 'noneTitle', help: 'noneHelp' },
} as const satisfies Record<MCPCredentialScope, { title: string; help: string }>

export function MCPServerForm({
  mode,
  initial,
  onSubmit,
  onTestConnection,
  onCancel,
}: MCPServerFormProps) {
  const t = useTranslations('mcp.form')
  const tScope = useTranslations('mcp.scopeForm')
  const [values, setValues] = useState<MCPServerFormValues>({
    ...DEFAULT_VALUES,
    credential_scope: mode === 'admin' ? 'org' : 'workspace',
    ...initial,
  })
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<MCPTestConnectionResult | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const set = <K extends keyof MCPServerFormValues>(key: K, value: MCPServerFormValues[K]) => {
    setValues((current) => ({ ...current, [key]: value }))
  }

  const scopes = mode === 'admin' ? adminScopes : workspaceScopes
  const requiresStoredSecret =
    values.credential_scope === 'org' || values.credential_scope === 'workspace'

  function onScopeChange(scope: MCPCredentialScope): void {
    setValues((current) => ({
      ...current,
      credential_scope: scope,
      auth_method: scope === 'none' ? 'none' : 'static',
      credential_plaintext:
        scope === 'user' || scope === 'none' ? '' : current.credential_plaintext,
    }))
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSubmitting(true)
    try {
      await onSubmit(values)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleTestConnection(): Promise<void> {
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await onTestConnection(values))
    } finally {
      setTesting(false)
    }
  }

  return (
    <form className="flex flex-col gap-6" onSubmit={handleSubmit}>
      <Card>
        <CardHeader>
          <CardTitle>{t('basicInfo')}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="mcp-name" className="text-sm font-medium">
              {t('nameLabel')}
            </label>
            <Input
              id="mcp-name"
              required
              value={values.name}
              onChange={(event) => set('name', event.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="mcp-url" className="text-sm font-medium">
              {t('serverUrlLabel')}
            </label>
            <Input
              id="mcp-url"
              required
              value={values.server_url}
              placeholder={t('serverUrlPlaceholder')}
              onChange={(event) => set('server_url', event.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">{t('transport')}</span>
            <Select
              value={values.transport}
              onValueChange={(value) => set('transport', value as MCPTransport)}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="streamable_http">streamable_http</SelectItem>
                  <SelectItem value="sse">sse</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('credentialMode')}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <RadioGroup
            value={values.credential_scope}
            onValueChange={(value) => onScopeChange(value as MCPCredentialScope)}
            className="flex flex-col gap-3"
          >
            {scopes.map((scope) => (
              <label
                key={scope}
                htmlFor={`mcp-scope-${scope}`}
                className="flex cursor-pointer items-start gap-3 rounded-lg border p-4 hover:bg-accent"
              >
                <RadioGroupItem value={scope} id={`mcp-scope-${scope}`} />
                <span className="flex flex-col gap-1">
                  <span className="font-medium">{tScope(scopeKey[scope].title)}</span>
                  <span className="text-sm text-muted-foreground">
                    {tScope(scopeKey[scope].help)}
                  </span>
                </span>
              </label>
            ))}
          </RadioGroup>

          {requiresStoredSecret ? (
            <div className="flex flex-col gap-3">
              <MCPSecretInput
                label={t('apiKeyLabel')}
                hasValue={false}
                required
                onChange={(token) => set('credential_plaintext', token)}
              />
              <div className="flex flex-col gap-1.5">
                <label htmlFor="mcp-credential-name" className="text-sm font-medium">
                  {t('credentialDisplayName')}
                </label>
                <Input
                  id="mcp-credential-name"
                  value={values.credential_name}
                  placeholder={`mcp:${values.name || 'server'}:${values.credential_scope}`}
                  onChange={(event) => set('credential_name', event.target.value)}
                />
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {testResult ? (
        <Alert variant={testResult.success ? 'default' : 'destructive'}>
          <AlertTitle>
            {testResult.success ? t('connectionSucceeded') : t('connectionFailed')}
          </AlertTitle>
          <AlertDescription>
            {testResult.success
              ? t('discovered', {
                  count: testResult.tools?.length ?? 0,
                  names: testResult.tools?.map((tool) => tool.name).join(', ') || t('noToolsName'),
                })
              : testResult.error || t('unknownError')}
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="flex items-center justify-between gap-3">
        <Button
          type="button"
          variant="outline"
          disabled={testing || !values.server_url}
          onClick={handleTestConnection}
        >
          {testing ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
          {t('testConnection')}
        </Button>
        <div className="flex items-center gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>
            {t('cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
            {t('save')}
          </Button>
        </div>
      </div>
    </form>
  )
}
