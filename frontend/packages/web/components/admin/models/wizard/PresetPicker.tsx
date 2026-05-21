'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Search } from 'lucide-react'
import { listPresets, type ApiClient, type ProviderPreset } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ProviderLogo } from '@/components/admin/models/ProviderLogo'
import { cn } from '@/lib/utils'

type Category = 'all' | 'saas' | 'oss-framework' | 'custom'
type ReasoningShapeKey = 'reasoningBudget' | 'reasoningEffort' | 'reasoningStandard'

interface PresetPickerProps {
  client: ApiClient
  selectedSlug: string | null
  onPick: (preset: ProviderPreset) => void
}

// The reasoning "shape" badge is derived from capability.reasoning_level.kind:
// int_budget → budget, effort/enum → effort, otherwise standard.
function reasoningShapeKey(preset: ProviderPreset): ReasoningShapeKey {
  const level = preset.capability.reasoning_level as { kind?: string } | undefined
  if (level?.kind === 'int_budget') return 'reasoningBudget'
  if (level?.kind === 'effort' || level?.kind === 'enum') return 'reasoningEffort'
  return 'reasoningStandard'
}

export function PresetPicker({ client, selectedSlug, onPick }: PresetPickerProps) {
  const t = useTranslations('adminModels.wizard.preset')
  const [presets, setPresets] = useState<ProviderPreset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<Category>('all')

  useEffect(() => {
    let cancelled = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch-on-mount loading flag
    setLoading(true)
    listPresets(client)
      .then((ps) => {
        if (!cancelled) setPresets(ps)
      })
      .catch(() => {
        if (!cancelled) setError(t('loadFailed'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client, t])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return presets.filter((p) => {
      if (category !== 'all' && p.category !== category) return false
      if (q && !p.display_name.toLowerCase().includes(q) && !p.slug.toLowerCase().includes(q)) {
        return false
      }
      return true
    })
  }, [presets, query, category])

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs value={category} onValueChange={(v) => setCategory(v as Category)}>
          <TabsList variant="line">
            <TabsTrigger value="all">{t('categoryAll')}</TabsTrigger>
            <TabsTrigger value="saas">{t('categoryHosted')}</TabsTrigger>
            <TabsTrigger value="oss-framework">{t('categorySelfHosted')}</TabsTrigger>
            <TabsTrigger value="custom">{t('categoryCustom')}</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="relative w-full max-w-[260px]">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('searchPlaceholder')}
            aria-label={t('searchAriaLabel')}
            className="pl-8"
          />
        </div>
      </div>

      {loading ? (
        <p className="py-12 text-center text-sm text-muted-foreground">{t('loading')}</p>
      ) : error ? (
        <p className="py-12 text-center text-sm text-destructive">{error}</p>
      ) : filtered.length === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">{t('empty')}</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((preset) => {
            const selected = preset.slug === selectedSlug
            return (
              <button
                key={preset.slug}
                type="button"
                aria-label={preset.display_name}
                aria-pressed={selected}
                onClick={() => onPick(preset)}
                className={cn(
                  'group flex flex-col gap-2 rounded-lg border bg-card p-4 text-left transition-all hover:border-primary/50 hover:shadow-sm',
                  selected ? 'border-primary ring-1 ring-primary/30' : 'border-border/70',
                )}
              >
                <div className="flex items-center gap-2.5">
                  <ProviderLogo
                    name={preset.display_name}
                    logoUrl={null}
                    logo={preset.logo}
                    size="lg"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold leading-tight">
                      {preset.display_name}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">{preset.api}</p>
                  </div>
                </div>
                <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground/80">
                  {preset.description}
                </p>
                <div className="mt-auto flex flex-wrap gap-1.5 pt-1">
                  <Badge variant="secondary" className="font-normal">
                    {t(reasoningShapeKey(preset))}
                  </Badge>
                </div>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
