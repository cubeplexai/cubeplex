'use client'

import { useTranslations } from 'next-intl'

import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import type { WizardStepProps } from '../platforms/types'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

export function StepCredentials({
  descriptor,
  form,
  onChange,
}: WizardStepProps): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT
  return (
    <div className="grid grid-cols-2 gap-3">
      {descriptor.credentialFields.map((f) => {
        if (f.showIf && !f.showIf(form)) return null
        if (f.type === 'select' && f.options) {
          return (
            <div key={f.key} className="space-y-1">
              <Label htmlFor={`cred-${f.key}`}>{t(f.labelKey)}</Label>
              <Select
                value={form[f.key] ?? ''}
                onValueChange={(v) => onChange({ [f.key]: v ?? '' })}
              >
                <SelectTrigger id={`cred-${f.key}`}>
                  <SelectValue placeholder="Select…" />
                </SelectTrigger>
                <SelectContent>
                  {f.options.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {t(o.labelKey)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )
        }
        return (
          <div key={f.key} className="space-y-1">
            <Label htmlFor={`cred-${f.key}`}>{t(f.labelKey)}</Label>
            <Input
              id={`cred-${f.key}`}
              type={f.type}
              required={f.required}
              placeholder={f.placeholder}
              value={form[f.key] ?? ''}
              onChange={(e) => onChange({ [f.key]: e.target.value })}
            />
          </div>
        )
      })}
    </div>
  )
}
