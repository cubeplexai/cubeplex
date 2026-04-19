'use client'

import { useRouter } from 'next/navigation'
import { useState, useRef, useEffect } from 'react'
import {
  createApiClient,
  logoutUser,
  useAuthStore,
  useConversationStore,
  useWorkspaceStore,
} from '@cubebox/core'

export function AvatarMenu() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const onLogout = async () => {
    const client = createApiClient('')
    try {
      await logoutUser(client)
    } catch {
      // logout is best-effort; proceed with local reset regardless
    }
    useAuthStore.getState().reset()
    useWorkspaceStore.getState().reset()
    useConversationStore.setState({ conversations: [], activeId: null })
    router.push('/login')
  }

  const initials = user?.email.slice(0, 2).toUpperCase() ?? '?'

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((s) => !s)}
        className="size-8 rounded-full bg-foreground/10 flex items-center justify-center text-xs font-medium"
        aria-label="Account"
      >
        {initials}
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-56 rounded-md border border-border bg-background shadow-md py-1 z-20">
          {user && (
            <div className="px-3 py-2 text-xs text-foreground/60 truncate">{user.email}</div>
          )}
          <button
            type="button"
            onClick={onLogout}
            className="block w-full text-left px-3 py-1.5 text-sm hover:bg-foreground/5"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
