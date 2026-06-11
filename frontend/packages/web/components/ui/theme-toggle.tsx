'use client'

import { useTheme } from 'next-themes'
import { useTranslations } from 'next-intl'
import { Button } from './button'
import { Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'

export function ThemeToggle() {
  const t = useTranslations('avatar')
  const { theme, resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true)
  }, [])

  if (!mounted) return null

  // Theme is two-axis: flavor (default | operator) × mode (light | dark).
  // This button toggles mode only, preserving the active flavor.
  const isOperator = theme === 'operator-light' || theme === 'operator-dark'
  const currentMode =
    theme === 'operator-light' || theme === 'light'
      ? 'light'
      : theme === 'operator-dark' || theme === 'dark'
        ? 'dark'
        : (resolvedTheme ?? 'light')
  const label = currentMode === 'dark' ? t('lightTheme') : t('darkTheme')
  const next = currentMode === 'dark' ? 'light' : 'dark'
  const onClick = () => setTheme(isOperator ? `operator-${next}` : next)

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
