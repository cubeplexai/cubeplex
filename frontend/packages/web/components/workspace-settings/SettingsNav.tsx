'use client'

import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { Bot, Plug, Sparkles, Users } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useWorkspaceStore } from '@cubebox/core'

interface SettingsNavProps {
  wsId: string
}

type TopLabelKey =
  | 'navWorkspace'
  | 'navPersona'
  | 'navModel'
  | 'navSkills'
  | 'navMcp'
  | 'navMembers'

interface SubItem {
  key: string
  labelKey: TopLabelKey
  disabled?: boolean
}

interface TopItem {
  key: string
  labelKey: TopLabelKey
  icon: typeof Bot
  sub?: SubItem[]
}

const BASE_TOP_LEVEL: TopItem[] = [
  {
    key: 'workspace',
    labelKey: 'navWorkspace',
    icon: Bot,
    sub: [
      { key: 'persona', labelKey: 'navPersona' },
      { key: 'model', labelKey: 'navModel', disabled: true },
    ],
  },
  { key: 'skills', labelKey: 'navSkills', icon: Sparkles },
  { key: 'mcp', labelKey: 'navMcp', icon: Plug },
]

const MEMBERS_ITEM: TopItem = { key: 'members', labelKey: 'navMembers', icon: Users }

export function SettingsNav({ wsId }: SettingsNavProps): React.ReactElement {
  const t = useTranslations('wsSettings')
  const searchParams = useSearchParams()
  const currentTab = searchParams.get('tab') ?? 'workspace'
  const currentSub = searchParams.get('sub') ?? 'persona'
  const wsRole = useWorkspaceStore((s) => s.workspaces.find((w) => w.id === wsId)?.role)
  const topLevel = wsRole === 'admin' ? [...BASE_TOP_LEVEL, MEMBERS_ITEM] : BASE_TOP_LEVEL

  return (
    <div className="px-2 pt-2 pb-2 border-t border-border/60 bg-muted/20">
      <p className="px-2 pt-1 pb-1.5 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
        {t('settingsLabel')}
      </p>
      <nav className="space-y-0.5">
        {topLevel.map((item) => {
          const Icon = item.icon
          const isActive = currentTab === item.key
          return (
            <div key={item.key}>
              <Link
                href={`/w/${wsId}/settings?tab=${item.key}${item.sub ? `&sub=${item.sub[0].key}` : ''}`}
                className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[12.5px] transition-colors ${
                  isActive
                    ? 'text-primary bg-primary/10 font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
              >
                <Icon className="size-3.5 shrink-0" />
                {t(item.labelKey)}
              </Link>
              {isActive && item.sub && (
                <div className="ml-6 mt-0.5 space-y-0.5">
                  {item.sub.map((s) => {
                    const isDisabled = s.disabled
                    const itemClass = `flex items-center justify-between px-2 py-1 rounded-md text-[11.5px] transition-colors ${
                      currentSub === s.key
                        ? 'text-primary font-medium'
                        : isDisabled
                          ? 'text-muted-foreground/40 cursor-default'
                          : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                    }`
                    if (isDisabled) {
                      return (
                        <span key={s.key} className={itemClass} aria-disabled="true">
                          {t(s.labelKey)}
                          <span className="text-[9px] bg-muted text-muted-foreground/60 rounded px-1">
                            {t('soonBadge')}
                          </span>
                        </span>
                      )
                    }
                    return (
                      <Link
                        key={s.key}
                        href={`/w/${wsId}/settings?tab=${item.key}&sub=${s.key}`}
                        className={itemClass}
                      >
                        {t(s.labelKey)}
                      </Link>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </nav>
    </div>
  )
}
