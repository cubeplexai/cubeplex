'use client'

import { useRouter } from 'next/navigation'
import { useLocale, useTranslations } from 'next-intl'
import { ChevronDown, Languages } from 'lucide-react'

type SupportedLocale = 'en' | 'zh'

function normalizeLocale(locale: string): SupportedLocale {
  return locale === 'zh' ? 'zh' : 'en'
}

export function AuthLanguageSwitcher() {
  const router = useRouter()
  const locale = normalizeLocale(useLocale())
  const t = useTranslations('avatar')

  const onLanguageChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    const nextLocale = normalizeLocale(event.target.value)
    document.cookie = `NEXT_LOCALE=${nextLocale}; path=/; SameSite=Lax`
    router.refresh()
  }

  return (
    <label className="absolute right-5 top-5 z-20 md:right-8 md:top-7">
      <span className="sr-only">{t('language')}</span>
      <span className="relative inline-flex h-8 items-center">
        <Languages className="pointer-events-none absolute left-2.5 size-3.5 text-muted-foreground" />
        <select
          aria-label={t('language')}
          value={locale}
          onChange={onLanguageChange}
          className="h-8 appearance-none rounded-md border border-border/80 bg-background/75 pl-8 pr-7 text-xs font-medium text-foreground shadow-sm outline-none transition-colors duration-fast hover:bg-background focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <option value="en">EN</option>
          <option value="zh">中文</option>
        </select>
        <ChevronDown className="pointer-events-none absolute right-2 size-3 text-muted-foreground" />
      </span>
    </label>
  )
}
