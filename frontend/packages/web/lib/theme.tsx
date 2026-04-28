'use client'

import { useEffect, useState } from 'react'
import { create } from 'zustand'

interface ThemeStore {
  theme: 'dark' | 'light'
  toggle(): void
}

export const useThemeStore = create<ThemeStore>((set) => ({
  theme: 'light',
  toggle() {
    set((s) => {
      const newTheme = s.theme === 'dark' ? 'light' : 'dark'
      if (typeof document !== 'undefined') {
        document.documentElement.classList.toggle('light', newTheme === 'light')
        localStorage.setItem('theme', newTheme)
      }
      return { theme: newTheme }
    })
  },
}))

export function useThemeInitializer() {
  const [mounted, setMounted] = useState(false)
  const { theme, toggle } = useThemeStore()

  useEffect(() => {
    const stored = localStorage.getItem('theme') as 'dark' | 'light' | null
    const initial = stored || 'light'
    if (initial !== theme) {
      toggle()
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return mounted
}
