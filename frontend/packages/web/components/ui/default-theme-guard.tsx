'use client'

import { useTheme } from 'next-themes'
import { useEffect } from 'react'
import { remapOperatorTheme } from '@/lib/themeFlavor'

/** Remap leftover operator-* storage to the default family. */
export function DefaultThemeGuard() {
  const { theme, setTheme } = useTheme()
  useEffect(() => {
    const next = remapOperatorTheme(theme)
    if (next) setTheme(next)
  }, [theme, setTheme])
  return null
}
