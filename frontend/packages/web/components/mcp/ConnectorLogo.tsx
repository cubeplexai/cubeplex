'use client'

import * as React from 'react'
import { useState } from 'react'
import { cn } from '@/lib/utils'

export interface ConnectorIconEntry {
  src: string
  mime_type?: string | null
  sizes?: string[] | null
  theme?: string | null
  /** Offline-safe data URI materialised at discovery time. Prefer over src. */
  cached_src?: string | null
}

export interface ConnectorLogoProps {
  /** Display name — used for letter avatar + alt text. */
  name: string
  /** Catalog brand key → `/mcp-icons/{icon}.svg`. */
  icon?: string | null
  /** Discovery / server icons (may include remote https + cached_src). */
  serverIcons?: ConnectorIconEntry[] | null
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const SIZE_CLASS = {
  sm: 'size-6 text-[10px]',
  md: 'size-8 text-xs',
  lg: 'size-10 text-sm',
} as const

const AVATAR_COLORS = [
  { bg: 'bg-info-surface', text: 'text-info-fg' },
  { bg: 'bg-success-surface', text: 'text-success-fg' },
  { bg: 'bg-muted', text: 'text-muted-foreground' },
  { bg: 'bg-warning-surface', text: 'text-warning-fg' },
  { bg: 'bg-danger-surface', text: 'text-danger-fg' },
  { bg: 'bg-accent', text: 'text-accent-foreground' },
] as const

function hashName(name: string): number {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return hash
}

function allowRemoteIcons(): boolean {
  // Default true so online users get vendor logos. Air-gapped deploys set
  // NEXT_PUBLIC_MCP_ALLOW_REMOTE_ICONS=false.
  const raw = process.env.NEXT_PUBLIC_MCP_ALLOW_REMOTE_ICONS
  if (raw === undefined || raw === '') return true
  return raw !== '0' && raw.toLowerCase() !== 'false'
}

/** Pick the best renderable src from a discovery icon entry. */
export function pickIconSrc(
  icon: ConnectorIconEntry | null | undefined,
  opts?: { allowRemote?: boolean },
): string | null {
  const allowRemote = opts?.allowRemote ?? allowRemoteIcons()
  if (!icon) return null
  if (icon.cached_src) return icon.cached_src
  const src = icon.src
  if (!src) return null
  if (src.startsWith('data:image/') || src.startsWith('/')) return src
  if (allowRemote && (src.startsWith('https://') || src.startsWith('http://'))) {
    return src
  }
  return null
}

export function pickServerIconSrc(
  serverIcons: ConnectorIconEntry[] | null | undefined,
  opts?: { allowRemote?: boolean },
): string | null {
  const allowRemote = opts?.allowRemote ?? allowRemoteIcons()
  for (const icon of serverIcons ?? []) {
    const src = pickIconSrc(icon, { allowRemote })
    if (src) return src
  }
  return null
}

function catalogIconSrc(icon: string | null | undefined): string | null {
  if (!icon) return null
  // Same validation as backend template_icon_key — prevent path traversal.
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(icon)) return null
  return `/mcp-icons/${icon}.svg`
}

/**
 * Connector brand mark for catalog / install lists.
 *
 * Resolve order:
 * 1. Catalog static asset `/mcp-icons/{icon}.svg`
 * 2. Discovery server icons (cached_src → data:/relative → optional https)
 * 3. Letter avatar from name
 */
export function ConnectorLogo({
  name,
  icon = null,
  serverIcons = null,
  size = 'sm',
  className,
}: ConnectorLogoProps): React.ReactElement {
  const candidates = React.useMemo(() => {
    const list: string[] = []
    const catalog = catalogIconSrc(icon)
    if (catalog) list.push(catalog)
    const remoteOk = allowRemoteIcons()
    for (const entry of serverIcons ?? []) {
      const src = pickIconSrc(entry, { allowRemote: remoteOk })
      if (src && !list.includes(src)) list.push(src)
    }
    return list
  }, [icon, serverIcons])

  const [index, setIndex] = useState(0)
  // Reset when the candidate list identity changes (new connector selected).
  const candidateKey = candidates.join('|')
  React.useEffect(() => {
    setIndex(0)
  }, [candidateKey])

  const src = candidates[index] ?? null
  const color = AVATAR_COLORS[hashName(name) % AVATAR_COLORS.length]
  const letter = (name.trim().charAt(0) || '?').toUpperCase()

  if (src) {
    return (
      <span
        className={cn(
          'relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-md',
          SIZE_CLASS[size],
          className,
        )}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt=""
          className="size-full object-contain"
          onError={() => setIndex((i) => i + 1)}
        />
      </span>
    )
  }

  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-md font-semibold',
        SIZE_CLASS[size],
        color.bg,
        color.text,
        className,
      )}
      aria-hidden
    >
      {letter}
    </span>
  )
}
