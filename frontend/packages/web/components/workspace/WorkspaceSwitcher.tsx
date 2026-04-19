'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useWorkspaceStore } from '@cubebox/core'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { ChevronDown, Plus } from 'lucide-react'
import { useState, useRef, useEffect } from 'react'

export function WorkspaceSwitcher() {
  const router = useRouter()
  const { workspaceId } = useWorkspaceContext()
  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const current = workspaces.find((w) => w.id === workspaceId)
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((s) => !s)}
        className="flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-foreground/5"
      >
        <span>{current?.name ?? 'Workspace'}</span>
        <ChevronDown className="size-4" />
      </button>
      {open && (
        <div className="absolute left-0 mt-1 w-56 rounded-md border border-border bg-background shadow-md py-1 z-20">
          {workspaces.map((w) => (
            <button
              key={w.id}
              type="button"
              className={`block w-full text-left px-3 py-1.5 text-sm hover:bg-foreground/5 ${w.id === workspaceId ? 'font-medium' : ''}`}
              onClick={() => {
                setOpen(false)
                router.push(`/w/${w.id}`)
              }}
            >
              {w.name}
            </button>
          ))}
          <div className="border-t border-border my-1" />
          <Link
            href="/workspaces"
            className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-foreground/5"
            onClick={() => setOpen(false)}
          >
            <Plus className="size-3.5" /> Manage workspaces
          </Link>
        </div>
      )}
    </div>
  )
}
