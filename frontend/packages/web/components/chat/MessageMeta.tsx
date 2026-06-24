'use client'

import { useState } from 'react'
import { useLocale, useTranslations } from 'next-intl'
import { Check, Copy } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { bucketRelativeTime, formatAbsoluteTime } from '@/lib/formatTime'

function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text)
  }
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

interface CopyButtonProps {
  // The text to copy. Empty string disables the button.
  content: string
}

export function CopyButton({ content }: CopyButtonProps) {
  const t = useTranslations('chat')
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    if (!content) return
    try {
      await copyText(content)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Silent — clipboard failure is rare and the tooltip itself isn't actionable.
    }
  }

  const label = copied ? t('copied') : t('copy')
  return (
    <button
      type="button"
      onClick={handleCopy}
      disabled={!content}
      aria-label={label}
      className="group/chip inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs
        text-muted-foreground hover:text-foreground hover:bg-muted/60
        disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent
        disabled:hover:text-muted-foreground transition-colors"
    >
      {copied ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
      <span className="hidden group-hover/chip:inline">{label}</span>
    </button>
  )
}

interface TimeChipProps {
  // cubepi Message.timestamp convention (epoch seconds). Null renders nothing.
  timestamp: number | null | undefined
}

export function TimeChip({ timestamp }: TimeChipProps) {
  const t = useTranslations('chat.time')
  const locale = useLocale()
  const bucket = bucketRelativeTime(timestamp)
  if (!bucket || timestamp == null) return null

  let relative: string
  switch (bucket.kind) {
    case 'justNow':
      relative = t('justNow')
      break
    case 'minutes':
      relative = t('minutesAgo', { n: bucket.n })
      break
    case 'hours':
      relative = t('hoursAgo', { n: bucket.n })
      break
    case 'days':
      relative = t('daysAgo', { n: bucket.n })
      break
    case 'date':
      relative = bucket.date.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
      break
  }
  const absolute = formatAbsoluteTime(timestamp, locale)

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          type="button"
          className="inline-flex items-center rounded-md px-2 py-1 text-xs font-mono
            tabular-nums text-muted-foreground/80 hover:text-foreground
            hover:bg-muted/60 transition-colors"
        >
          {relative}
        </TooltipTrigger>
        <TooltipContent>{absolute}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
