'use client'

import { ExternalLink } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Checkbox } from '@/components/ui/checkbox'

import type { WizardStepProps } from '../platforms/types'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

export function StepPrereqs({ descriptor, form, onChange }: WizardStepProps): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT
  return (
    <ul className="space-y-3 text-sm">
      {descriptor.prereqs.map((p) => (
        <li key={p.key} className="flex items-start gap-3">
          <Checkbox
            id={`prereq-${p.key}`}
            checked={form[`prereq_${p.key}`] === '1'}
            onCheckedChange={(c) => onChange({ [`prereq_${p.key}`]: c === true ? '1' : '' })}
          />
          <label htmlFor={`prereq-${p.key}`} className="flex-1">
            {t(p.labelKey)}
          </label>
          {p.helpUrl && (
            <a
              href={p.helpUrl(form)}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
              aria-describedby={`prereq-${p.key}-extlink`}
            >
              <ExternalLink className="size-3" />
              <span id={`prereq-${p.key}-extlink`} className="sr-only">
                Opens external site in new tab
              </span>
            </a>
          )}
        </li>
      ))}
    </ul>
  )
}
