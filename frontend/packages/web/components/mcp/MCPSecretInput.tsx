'use client'

import { useId, useState } from 'react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export interface MCPSecretInputProps {
  label: string
  hasValue: boolean
  onChange: (plaintext: string) => void
  required?: boolean
}

export function MCPSecretInput({
  label,
  hasValue,
  onChange,
  required = false,
}: MCPSecretInputProps) {
  const t = useTranslations('mcp.secret')
  const inputId = useId()
  const [editing, setEditing] = useState(!hasValue)
  const [value, setValue] = useState('')

  if (!editing) {
    return (
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium">{label}</span>
        <span className="font-mono text-sm text-muted-foreground">{t('set')}</span>
        <Button type="button" variant="outline" size="sm" onClick={() => setEditing(true)}>
          {t('replace')}
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={inputId} className="text-sm font-medium">
        {label}
        {required ? <span className="ml-0.5 text-destructive">*</span> : null}
      </label>
      <div className="flex items-center gap-2">
        <Input
          id={inputId}
          type="password"
          autoComplete="new-password"
          value={value}
          onChange={(event) => {
            setValue(event.target.value)
            onChange(event.target.value)
          }}
          placeholder={t('apiKeyPlaceholder')}
          required={required && !hasValue}
        />
        {hasValue ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setValue('')
              onChange('')
              setEditing(false)
            }}
          >
            {t('cancel')}
          </Button>
        ) : null}
      </div>
      <p className="text-xs text-muted-foreground">{t('writeOnly')}</p>
    </div>
  )
}
