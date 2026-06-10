'use client'

import { useTheme } from 'next-themes'
import { useTranslations } from 'next-intl'
import { Button } from './button'
import { Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'

export function ThemeToggle() {
  const t = useTranslations('avatar')
  const { resolvedTheme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true)
  }, [])

  if (!mounted) return null

  const label = resolvedTheme === 'dark' ? t('lightTheme') : t('darkTheme')

  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={label}
      title={label}
      onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
    >
      {resolvedTheme === 'dark' ? (
        <Sun aria-hidden className="size-4" />
      ) : (
        <Moon aria-hidden className="size-4" />
      )}
    </Button>
  )
}
