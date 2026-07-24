'use client'

import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { formatSkillLabel } from '@cubeplex/core'

/**
 * When `name` is namespaced (`org:slug`), renders a mono secondary row with
 * the full canonical identity and a copy control. Returns null for bare names.
 */
export function SkillCanonicalNameRow({
  name,
  copyLabel,
  copiedLabel,
}: {
  name: string
  copyLabel: string
  copiedLabel: string
}) {
  const label = formatSkillLabel(name)
  const [copied, setCopied] = useState(false)

  if (!label.isNamespaced) return null

  async function handleCopy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(label.canonical)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard may be unavailable; the mono text remains selectable
    }
  }

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <span
        className="truncate font-mono text-xs text-muted-foreground"
        title={label.canonical}
        data-testid="skill-canonical-name"
      >
        {label.canonical}
      </span>
      <button
        type="button"
        onClick={() => void handleCopy()}
        className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
        title={copied ? copiedLabel : copyLabel}
        aria-label={copied ? copiedLabel : copyLabel}
      >
        {copied ? <Check className="size-3 text-success-fg" /> : <Copy className="size-3" />}
      </button>
    </div>
  )
}
