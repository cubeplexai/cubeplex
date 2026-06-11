'use client'

import { create } from 'zustand'

interface MobileMenuState {
  isOpen: boolean
  open: () => void
  close: () => void
  set: (open: boolean) => void
}

/** Tiny module-level store so AppShell's header (which lives inside the
 *  main column) can pop the sidebar drawer that AppLayout owns. */
export const useMobileMenu = create<MobileMenuState>((set) => ({
  isOpen: false,
  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),
  set: (open) => set({ isOpen: open }),
}))
