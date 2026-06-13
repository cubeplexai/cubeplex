'use client'

import { Plus } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'

interface Props {
  showConnect: boolean
  onConnect: () => void
  count: number
}

/**
 * Toolbar above the IM accounts list. Workspace scope shows the
 * Connect CTA; admin scope leaves it hidden (the admin doesn't bind
 * bots — workspaces do).
 */
export function ImAccountToolbar({ showConnect, onConnect, count }: Props): React.ReactElement {
  const t = useTranslations('im')
  return (
    <div className="flex items-center justify-between border-b border-border/50 px-3 py-2">
      <span className="text-xs text-muted-foreground">
        {count} {count === 1 ? 'account' : 'accounts'}
      </span>
      {showConnect && (
        <Button size="sm" onClick={onConnect}>
          <Plus className="size-3.5" />
          {t('action.connect')}
        </Button>
      )}
    </div>
  )
}
