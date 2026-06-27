'use client'

import { useMemo, useEffect, useRef } from 'react'
import { createAvatar } from '@dicebear/core'
import { notionists, bottts } from '@dicebear/collection'
import { createApiClient, uploadAvatar } from '@cubebox/core'
import { initials as toInitials, avatarColor } from '@/lib/avatar'
import { Avatar as AvatarRoot, AvatarImage, AvatarFallback } from './avatar'

export interface AvatarProps {
  /** Real image URL (S3, SSO, uploaded). When present, shown first. */
  src?: string | null
  /** DiceBear seed for deterministic generated avatar. */
  seed?: string | null
  /** Display name, used for initials fallback. */
  name?: string | null
  /** DiceBear style. Defaults to 'notionists'. */
  style?: 'notionists' | 'bottts'
  /** Shadcn size preset. Maps to DiceBear pixel size internally. */
  size?: 'default' | 'sm' | 'lg'
  /** User ID — used as fallback seed when `seed` is null. */
  userId?: string
  /**
   * When true, `src` is null, and `userId` is present: fire a one-shot
   * background uploadAvatar({ kind:'generated', seed, style }) to
   * materialize the PNG so IM / email get a stable URL.
   * The live DiceBear render still shows meanwhile.
   */
  selfHeal?: boolean
  className?: string
}

const SIZE_MAP: Record<string, number> = { sm: 24, default: 32, lg: 40 }

export function Avatar({
  src,
  seed,
  name,
  style = 'notionists',
  size = 'default',
  userId,
  selfHeal,
  className,
}: AvatarProps) {
  const healed = useRef(false)
  const effectiveSeed = seed ?? userId ?? name ?? 'unknown'
  const pixelSize = SIZE_MAP[size] ?? 32

  const svgDataUri = useMemo(() => {
    const collection = style === 'bottts' ? bottts : notionists
    return createAvatar(collection as never, { seed: effectiveSeed, size: pixelSize }).toDataUri()
  }, [effectiveSeed, style, pixelSize])

  useEffect(() => {
    if (!selfHeal || healed.current || src || !userId) return
    healed.current = true
    void (async () => {
      try {
        const collection = style === 'bottts' ? bottts : notionists
        const svg = createAvatar(collection as never, { seed: effectiveSeed, size: 256 }).toString()
        const { svgToPngBlob } = await import('@/lib/avatar')
        const png = await svgToPngBlob(svg, 256)
        const client = createApiClient('')
        await uploadAvatar(client, {
          file: new File([png], 'avatar.png'),
          kind: 'generated',
          seed: effectiveSeed,
          style,
        })
      } catch {
        // best-effort; the live render still shows correctly
      }
    })()
  }, [selfHeal, src, userId, effectiveSeed, style])

  return (
    <AvatarRoot className={className} size={size}>
      {src ? <AvatarImage src={src} alt={name ?? ''} /> : <AvatarImage src={svgDataUri} alt="" />}
      <AvatarFallback style={{ backgroundColor: avatarColor(effectiveSeed) }}>
        {name ? toInitials(name) : ''}
      </AvatarFallback>
    </AvatarRoot>
  )
}
