'use client'

import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { Bot, Plug, Sparkles } from 'lucide-react'

interface SettingsNavProps {
  wsId: string
}

const TOP_LEVEL = [
  {
    key: 'workspace',
    label: 'Workspace Settings',
    icon: Bot,
    sub: [
      { key: 'persona', label: 'Persona' },
      { key: 'model', label: 'Model', disabled: true },
    ],
  },
  { key: 'skills', label: 'Skills', icon: Sparkles },
  { key: 'mcp', label: 'MCP Connectors', icon: Plug },
]

export function SettingsNav({ wsId }: SettingsNavProps): React.ReactElement {
  const searchParams = useSearchParams()
  const currentTab = searchParams.get('tab') ?? 'workspace'
  const currentSub = searchParams.get('sub') ?? 'persona'

  return (
    <div className="px-2 pt-3 pb-2">
      <p className="px-2 text-[9px] font-semibold uppercase tracking-widest text-muted-foreground/50 mb-2">
        Settings
      </p>
      <nav className="space-y-0.5">
        {TOP_LEVEL.map((item) => {
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
                {item.label}
              </Link>
              {isActive && item.sub && (
                <div className="ml-6 mt-0.5 space-y-0.5">
                  {item.sub.map((s) => (
                    <Link
                      key={s.key}
                      href={
                        'disabled' in s && s.disabled
                          ? '#'
                          : `/w/${wsId}/settings?tab=${item.key}&sub=${s.key}`
                      }
                      className={`flex items-center justify-between px-2 py-1 rounded-md text-[11.5px] transition-colors ${
                        currentSub === s.key
                          ? 'text-primary font-medium'
                          : 'disabled' in s && s.disabled
                            ? 'text-muted-foreground/40 cursor-default pointer-events-none'
                            : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                      }`}
                    >
                      {s.label}
                      {'disabled' in s && s.disabled && (
                        <span className="text-[9px] bg-muted text-muted-foreground/60 rounded px-1">
                          soon
                        </span>
                      )}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </nav>
    </div>
  )
}
