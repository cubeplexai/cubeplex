'use client'

import { useTheme } from 'next-themes'
import { useTranslations } from 'next-intl'
import { Button } from './button'
import { Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { resolveColorMode } from '@/lib/themeFlavor'

export function ThemeToggle() {
  const t = useTranslations('avatar')
  const { theme, resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true)
  }, [])

  if (!mounted) return null

  // Product UI writes the default family only (light | dark). Operator names
  // still resolve so a leftover stored value shows the right icon.
  const currentMode = resolveColorMode(theme, resolvedTheme)
  const label = currentMode === 'dark' ? t('lightTheme') : t('darkTheme')
  const next = currentMode === 'dark' ? 'light' : 'dark'
  const onClick = () => setTheme(next)

  return (
    <Button variant="ghost" size="sm" aria-label={label} title={label} onClick={onClick}>
      {currentMode === 'dark' ? (
        <Sun aria-hidden className="size-4" />
      ) : (
        <Moon aria-hidden className="size-4" />
      )}
    </Button>
  )
}
