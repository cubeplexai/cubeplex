'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Wand2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverTitle,
  PopoverTrigger,
} from '@/components/ui/popover'
import { cn } from '@/lib/utils'

// A minimal effort-style reasoning block users can drop into a custom preset.
const REASONING_TEMPLATE = {
  reasoning: {
    mode_payloads: {
      off: {},
      on: {},
    },
    effort_path: 'reasoning_effort',
    effort_values: {
      minimal: 'minimal',
      low: 'low',
      medium: 'medium',
      high: 'high',
      max: 'high',
    },
    apply_effort_when_off: false,
  },
}

interface CapabilityEditorProps {
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
  allowTemplate?: boolean
}

export function CapabilityEditor({
  value,
  onChange,
  allowTemplate = false,
}: CapabilityEditorProps) {
  const t = useTranslations('adminModels.wizard.capability')
  const [text, setText] = useState(() => JSON.stringify(value, null, 2))
  const [error, setError] = useState<string | null>(null)

  function handleText(next: string) {
    setText(next)
    try {
      const parsed = JSON.parse(next) as Record<string, unknown>
      setError(null)
      onChange(parsed)
    } catch {
      setError(t('invalid'))
    }
  }

  function injectTemplate() {
    let base: Record<string, unknown> = {}
    try {
      base = JSON.parse(text) as Record<string, unknown>
    } catch {
      base = {}
    }
    const merged = { ...base, ...REASONING_TEMPLATE }
    const next = JSON.stringify(merged, null, 2)
    setText(next)
    setError(null)
    onChange(merged)
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <Label htmlFor="capability-json">{t('label')}</Label>
        {allowTemplate && (
          <Popover>
            <PopoverTrigger
              render={
                <Button type="button" variant="ghost" size="sm" className="h-7 gap-1.5 text-xs">
                  <Wand2 className="size-3.5" />
                  {t('useTemplate')}
                </Button>
              }
            />
            <PopoverContent className="w-64">
              <PopoverTitle>{t('templateReasoning')}</PopoverTitle>
              <PopoverDescription className="mt-1">{t('templateHint')}</PopoverDescription>
              <Button type="button" size="sm" className="mt-3 w-full" onClick={injectTemplate}>
                {t('templateReasoning')}
              </Button>
            </PopoverContent>
          </Popover>
        )}
      </div>
      <textarea
        id="capability-json"
        value={text}
        onChange={(e) => handleText(e.target.value)}
        spellCheck={false}
        rows={10}
        className={cn(
          'w-full rounded-lg border bg-transparent px-2.5 py-1.5 font-mono text-xs leading-relaxed outline-none focus-visible:ring-3 focus-visible:ring-ring/50',
          error
            ? 'border-destructive focus-visible:border-destructive'
            : 'border-input focus-visible:border-ring',
        )}
      />
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  )
}
