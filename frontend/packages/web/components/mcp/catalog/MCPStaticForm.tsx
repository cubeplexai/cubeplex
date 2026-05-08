'use client'

import { useId, useMemo, useState } from 'react'
import type { MCPCatalogStaticFormField } from '@cubebox/core'
import { Eye, EyeOff, Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export interface MCPStaticFormProps {
  fields: MCPCatalogStaticFormField[]
  onSubmit: (values: Record<string, string>) => Promise<void>
  submitting: boolean
}

interface FieldRowProps {
  field: MCPCatalogStaticFormField
  value: string
  onChange: (next: string) => void
  showSecretLabel: string
  hideSecretLabel: string
  helperLinkLabel: string
}

function FieldRow({
  field,
  value,
  onChange,
  showSecretLabel,
  hideSecretLabel,
  helperLinkLabel,
}: FieldRowProps) {
  const inputId = useId()
  const [reveal, setReveal] = useState(false)
  const inputType = field.secret && !reveal ? 'password' : 'text'

  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={inputId}>
        {field.label}
        <span className="ml-0.5 text-destructive">*</span>
      </Label>
      <div className="flex items-stretch gap-2">
        <Input
          id={inputId}
          type={inputType}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={field.placeholder}
          autoComplete={field.secret ? 'new-password' : 'off'}
          required
        />
        {field.secret ? (
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={() => setReveal((prev) => !prev)}
            aria-label={reveal ? hideSecretLabel : showSecretLabel}
          >
            {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </Button>
        ) : null}
      </div>
      {field.helper_url ? (
        <a
          href={field.helper_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-primary hover:underline"
        >
          {helperLinkLabel}
        </a>
      ) : null}
    </div>
  )
}

// TODO(atlassian): multi-field static form. The backend currently consumes a
// single `credential_plaintext` string and does not yet apply
// `static_auth_header_template` to combine multiple fields (e.g. atlassian's
// email + api_token => "Basic b64(email:api_token)"). Until the backend
// supports template rendering, this form refuses to submit when more than one
// field is declared.
export function MCPStaticForm({ fields, onSubmit, submitting }: MCPStaticFormProps) {
  const t = useTranslations('mcpCatalog')
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.name, ''])),
  )
  const [validationError, setValidationError] = useState<string | null>(null)

  const multiField = fields.length > 1
  const trimmedValues = useMemo(
    () => Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v.trim()])),
    [values],
  )
  const allFilled = fields.every((field) => trimmedValues[field.name].length > 0)

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (multiField) {
      // Backend doesn't yet apply static_auth_header_template; refuse rather
      // than send an unsupported credential shape.
      setValidationError(t('multiFieldUnsupported'))
      return
    }
    if (!allFilled) {
      setValidationError(t('staticAllFieldsRequired'))
      return
    }
    setValidationError(null)
    await onSubmit(trimmedValues)
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={(e) => void handleSubmit(e)}>
      {multiField ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          {t('multiFieldUnsupported')}
        </p>
      ) : null}
      {fields.map((field) => (
        <FieldRow
          key={field.name}
          field={field}
          value={values[field.name] ?? ''}
          onChange={(next) =>
            setValues((prev) => ({
              ...prev,
              [field.name]: next,
            }))
          }
          showSecretLabel={t('showSecret')}
          hideSecretLabel={t('hideSecret')}
          helperLinkLabel={t('helperLearnMore')}
        />
      ))}
      {validationError ? <p className="text-sm text-destructive">{validationError}</p> : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={submitting || multiField || !allFilled}>
          {submitting ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
          {t('installButton')}
        </Button>
      </div>
    </form>
  )
}
