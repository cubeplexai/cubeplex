'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Check, Copy, Info } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

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

interface RunInfoChipProps {
  // cubepi run id for this turn. Null/empty hides the chip (legacy rows).
  runId: string | null | undefined
}

/**
 * Hover-revealed "info" chip on an assistant turn. Opens a popover that
 * surfaces the run_id so a human can paste it into ``cubepi trace`` / the
 * admin traces filter. No network call — the id is already on the message.
 */
export function RunInfoChip({ runId }: RunInfoChipProps) {
  const t = useTranslations('chat.runInfo')
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  if (!runId) return null

  const handleCopy = async () => {
    try {
      await copyText(runId)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard failures are rare; leave the id visible so the user can select it.
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            aria-label={t('ariaLabel')}
            className="group/chip inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs
              text-muted-foreground hover:text-foreground hover:bg-muted/60
              transition-colors"
          >
            <Info aria-hidden className="size-3.5" />
            <span className="hidden group-hover/chip:inline">{t('label')}</span>
          </button>
        }
      />
      <PopoverContent
        align="start"
        sideOffset={8}
        className="w-auto max-w-sm gap-0 space-y-1.5 border border-border px-3 py-2.5 text-xs
          text-muted-foreground"
      >
        <div className="font-medium text-foreground/70">{t('runIdLabel')}</div>
        <div className="flex items-center gap-1.5">
          <code
            className="min-w-0 flex-1 break-all font-mono text-[11px] leading-snug
              text-foreground/90 select-all"
          >
            {runId}
          </code>
          <button
            type="button"
            onClick={handleCopy}
            aria-label={copied ? t('copied') : t('copy')}
            className="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1
              text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
          >
            {copied ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
          </button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
