'use client'

import { useTranslations } from 'next-intl'
import { Search } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ArtifactsToolbarProps {
  types: string[]
  selectedType: string | null
  onSelectType: (type: string | null) => void
  search: string
  onSearch: (value: string) => void
}

export function ArtifactsToolbar({
  types,
  selectedType,
  onSelectType,
  search,
  onSearch,
}: ArtifactsToolbarProps): React.ReactElement {
  const t = useTranslations('artifactsPage')

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder={t('searchPlaceholder')}
          className="h-8 w-56 rounded-md border border-border bg-background pl-8 pr-3 text-sm
            outline-none transition-colors focus:border-primary/40"
          data-testid="artifacts-search"
        />
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Chip active={selectedType === null} onClick={() => onSelectType(null)}>
          {t('filterAll')}
        </Chip>
        {types.map((type) => (
          <Chip key={type} active={selectedType === type} onClick={() => onSelectType(type)}>
            {type}
          </Chip>
        ))}
      </div>
    </div>
  )
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}): React.ReactElement {
  return (
    <button
      onClick={onClick}
      className={cn(
        'rounded-full border px-2.5 py-1 text-xs capitalize transition-colors',
        active
          ? 'border-primary/40 bg-primary/10 text-foreground'
          : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent',
      )}
    >
      {children}
    </button>
  )
}
