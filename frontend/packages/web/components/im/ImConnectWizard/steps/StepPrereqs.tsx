'use client'

import { useEffect, useState } from 'react'
import { Check, Copy, ExternalLink } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'

import type { WizardStepProps } from '../platforms/types'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text)
  }
  // Fallback for non-HTTPS (e.g. dev over plain HTTP / IP access).
  return new Promise((resolve, reject) => {
    try {
      const el = document.createElement('textarea')
      el.value = text
      el.style.cssText = 'position:fixed;opacity:0;top:0;left:0'
      document.body.appendChild(el)
      el.focus()
      el.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(el)
      if (ok) {
        resolve()
      } else {
        reject(new Error('execCommand failed'))
      }
    } catch (e) {
      reject(e)
    }
  })
}

function CopyJsonButton({ json }: { json: string }) {
  const [copied, setCopied] = useState(false)

  function handleCopy() {
    copyText(json).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="mt-1.5 h-7 gap-1.5 px-2 text-xs"
      onClick={handleCopy}
    >
      {copied ? (
        <>
          <Check className="size-3 text-success" />
          Copied
        </>
      ) : (
        <>
          <Copy className="size-3" />
          Copy JSON
        </>
      )}
    </Button>
  )
}

export function StepPrereqs({ descriptor, form, onChange }: WizardStepProps): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT

  // If the descriptor has a domain field, render a picker here so help
  // links resolve to the right platform (feishu vs lark) before the user
  // reaches the credentials step.
  const domainField = descriptor.credentialFields.find((f) => f.key === 'domain')

  // Default to the first option (Lark) when the user hasn't picked yet.
  useEffect(() => {
    if (domainField?.options && !form.domain) {
      onChange({ domain: domainField.options[0].value })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="space-y-4 text-sm">
      {domainField?.options && (
        <div className="flex items-center gap-3 rounded-md border border-border/60 bg-muted/40 px-3 py-2">
          <Label htmlFor="prereq-domain-select" className="shrink-0 text-xs text-muted-foreground">
            {t(domainField.labelKey)}
          </Label>
          <Select value={form.domain || ''} onValueChange={(v) => v && onChange({ domain: v })}>
            <SelectTrigger id="prereq-domain-select" className="h-7 w-40 text-xs">
              <span className={form.domain ? '' : 'text-muted-foreground'}>
                {form.domain
                  ? t(
                      domainField.options?.find((o) => o.value === form.domain)?.labelKey ??
                        domainField.labelKey,
                    )
                  : t(domainField.labelKey)}
              </span>
            </SelectTrigger>
            <SelectContent>
              {domainField.options.map((o) => (
                <SelectItem key={o.value} value={o.value} className="text-xs">
                  {t(o.labelKey)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      <ul className="space-y-4">
        {descriptor.prereqs.map((p) => (
          <li key={p.key} className="flex items-start gap-3">
            <Checkbox
              id={`prereq-${p.key}`}
              checked={form[`prereq_${p.key}`] === '1'}
              onCheckedChange={(c) => onChange({ [`prereq_${p.key}`]: c === true ? '1' : '' })}
              className="mt-0.5 border-foreground/30"
            />
            <div className="flex-1 space-y-1.5">
              <div className="flex items-center gap-2">
                <label htmlFor={`prereq-${p.key}`} className="cursor-pointer">
                  {t(typeof p.labelKey === 'function' ? p.labelKey(form) : p.labelKey)}
                </label>
                {p.helpUrl && (
                  <a
                    href={p.helpUrl(form)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline"
                    aria-label="Open in Feishu console"
                  >
                    <ExternalLink className="size-3" />
                  </a>
                )}
              </div>
              {p.items && p.items.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {p.items.map((item) => (
                    <code
                      key={item}
                      className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
                    >
                      {item}
                    </code>
                  ))}
                </div>
              )}
              {p.copyJson && <CopyJsonButton json={p.copyJson} />}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
