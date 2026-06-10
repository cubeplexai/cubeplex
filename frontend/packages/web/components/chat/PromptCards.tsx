'use client'

import { useTranslations } from 'next-intl'
import { LineChart, Search, Workflow } from 'lucide-react'
import { useComposerDraft } from '@/hooks/useComposerDraft'

interface CardEntry {
  key: 'analyze' | 'research' | 'automate'
  icon: typeof LineChart
}

const CARDS: CardEntry[] = [
  { key: 'analyze', icon: LineChart },
  { key: 'research', icon: Search },
  { key: 'automate', icon: Workflow },
]

export function PromptCards() {
  const t = useTranslations('home.promptCards')
  const setDraft = useComposerDraft((s) => s.setDraft)

  return (
    <div className="grid w-full max-w-2xl grid-cols-1 gap-2 px-4 sm:grid-cols-3">
      {CARDS.map(({ key, icon: Icon }, i) => (
        <button
          key={key}
          type="button"
          onClick={() => setDraft(t(`${key}.prompt`))}
          style={{ animationDelay: `${i * 30}ms` }}
          className="group flex flex-col gap-1.5 rounded-lg border border-border bg-card p-3.5 text-left transition duration-fast hover:border-border-strong hover:bg-accent hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring animate-rise-in"
        >
          <Icon className="size-4 text-info-fg" strokeWidth={1.75} />
          <span className="text-sm font-medium text-foreground">{t(`${key}.title`)}</span>
          <span className="text-xs text-faint leading-relaxed">{t(`${key}.description`)}</span>
        </button>
      ))}
    </div>
  )
}
