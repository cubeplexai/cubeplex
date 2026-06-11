'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'

interface SettingsTabsProps {
  wsId: string
  active: string
}

const TABS = [
  { tab: 'workspace', labelKey: 'navPersona' },
  { tab: 'skills', labelKey: 'navSkills' },
  { tab: 'mcp', labelKey: 'navMcp' },
  { tab: 'members', labelKey: 'navMembers' },
  { tab: 'shares', labelKey: 'navShares' },
] as const

/**
 * Horizontal tab bar for the workspace settings page. The page renders one
 * panel per `?tab=` value; without this bar the sibling tabs are invisible
 * (the sidebar deep-links straight into individual tabs).
 */
export function SettingsTabs({ wsId, active }: SettingsTabsProps): React.ReactElement {
  const t = useTranslations('wsSettings')
  return (
    <nav
      aria-label={t('settingsLabel')}
      className="flex items-center gap-1 border-b border-border/70 px-6"
    >
      {TABS.map(({ tab, labelKey }) => (
        <Link
          key={tab}
          href={`/w/${wsId}/settings?tab=${tab}`}
          aria-current={active === tab ? 'page' : undefined}
          className={cn(
            '-mb-px border-b-2 px-3 py-2.5 text-sm transition-colors',
            active === tab
              ? 'border-primary font-medium text-foreground'
              : 'border-transparent text-muted-foreground hover:border-border hover:text-foreground',
          )}
        >
          {t(labelKey)}
        </Link>
      ))}
    </nav>
  )
}
