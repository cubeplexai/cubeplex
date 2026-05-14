'use client'

import { useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react'
import { useTranslations } from 'next-intl'

export interface ServerErrorBannerProps {
  error: string
}

const SHORT_LIMIT = 140

export function ServerErrorBanner({ error }: ServerErrorBannerProps) {
  const t = useTranslations('mcp.detail.errorBanner')
  const [expanded, setExpanded] = useState(false)
  const isLong = error.length > SHORT_LIMIT
  const visible = expanded || !isLong ? error : `${error.slice(0, SHORT_LIMIT)}…`

  return (
    <div className="flex gap-3 rounded-lg border-l-4 border-l-destructive bg-destructive/10 p-4 text-sm">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
      <div className="flex min-w-0 flex-col gap-1">
        <span className="font-medium text-destructive">{t('title')}</span>
        <p className="break-words text-destructive/90">{visible}</p>
        {isLong ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 self-start text-xs text-destructive/80 hover:text-destructive"
          >
            {expanded ? (
              <>
                <ChevronUp className="h-3 w-3" />
                {t('collapse')}
              </>
            ) : (
              <>
                <ChevronDown className="h-3 w-3" />
                {t('expand')}
              </>
            )}
          </button>
        ) : null}
      </div>
    </div>
  )
}
