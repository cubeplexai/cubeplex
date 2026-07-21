'use client'

import { useMemo, useEffect, useRef, useState } from 'react'
import { Avatar as DicebearAvatar, Style } from '@dicebear/core'
import glyphsDef from '@dicebear/styles/glyphs.json'
import notionistsDef from '@dicebear/styles/notionists.json'
import micahDef from '@dicebear/styles/micah.json'
import openPeepsDef from '@dicebear/styles/open-peeps.json'
import botttsDef from '@dicebear/styles/bottts.json'
import { avatarColor } from '@/lib/avatar'
import { cn } from '@/lib/utils'

/** DiceBear styles supported for human avatars. */
export type AvatarStyle = 'glyphs' | 'notionists' | 'micah' | 'open-peeps' | 'bottts'

// Style instances are reusable (the 10.x API deprecates passing raw definitions
// per-call), so build them once at module load.
const STYLE_INSTANCES: Record<AvatarStyle, Style<unknown>> = {
  glyphs: new Style(glyphsDef as never),
  notionists: new Style(notionistsDef as never),
  micah: new Style(micahDef as never),
  'open-peeps': new Style(openPeepsDef as never),
  bottts: new Style(botttsDef as never),
}

export interface AvatarProps {
  /** Real image URL (S3, SSO, uploaded). When present, shown first. */
  src?: string | null
  /** DiceBear seed for deterministic generated avatar. */
  seed?: string | null
  /** Display name, used for initials fallback. */
  name?: string | null
  /** DiceBear style. Defaults to 'glyphs' (gender-neutral). */
  style?: AvatarStyle
  /** Shadcn size preset. Maps to DiceBear pixel size internally. */
  size?: 'default' | 'sm' | 'lg' | 'xl'
  /** User ID — used as fallback seed when `seed` is null. */
  userId?: string
  /**
   * When true, render nothing visible (transparent placeholder) — used by
   * callers that know the avatar data is still loading (e.g. the current
   * user's avatar before /me resolves), to avoid a "default -> real" swap.
   */
  loading?: boolean
  className?: string
}

const SIZE_MAP: Record<string, number> = { sm: 24, default: 32, lg: 40, xl: 64 }
const SIZE_CLASS: Record<string, string> = {
  sm: 'size-6',
  default: 'size-8',
  lg: 'size-10',
  xl: 'size-16',
}

/**
 * Allow only safe URL schemes for an `<img src>`. Avatar URLs are S3/SSO
 * (`https://`), the same-origin proxy (`/api/v1/avatar/...`), a `blob:`, or a
 * DiceBear `data:image/...` URI. Anything else - `javascript:`, `vbscript:`,
 * `data:text/html` - is dropped so a malicious value can't reach a DOM sink;
 * the component falls back to the generated DiceBear avatar.
 */
const SAFE_AVATAR_SRC = /^(?:https?:|\/|blob:|data:image\/)/i
function safeAvatarSrc(src: string | null | undefined): string | undefined {
  if (!src) return undefined
  return SAFE_AVATAR_SRC.test(src) ? src : undefined
}

export function Avatar({
  src,
  seed,
  name,
  style = 'glyphs',
  size = 'default',
  userId,
  loading,
  className,
}: AvatarProps) {
  const effectiveSeed = seed ?? userId ?? name ?? 'unknown'
  const safeSrc = safeAvatarSrc(src)
  const pixelSize = SIZE_MAP[size] ?? 32

  const svgDataUri = useMemo(() => {
    const styleInstance = STYLE_INSTANCES[style] ?? STYLE_INSTANCES.glyphs
    return new DicebearAvatar(styleInstance, { seed: effectiveSeed, size: pixelSize }).toDataUri()
  }, [effectiveSeed, style, pixelSize])

  // Track whether the real `src` image has loaded. Until it has, show the
  // deterministic generated SVG (a data URI — instant, no network) so there
  // is never a flash to an empty/initials state. If `src` fails to load we
  // stay on the generated SVG. This avoids base-ui Avatar's two-phase
  // (fallback -> image) flicker on every src change.
  const [realLoaded, setRealLoaded] = useState(false)
  const [realFailed, setRealFailed] = useState(false)
  const realImgRef = useRef<HTMLImageElement>(null)
  useEffect(() => {
    // Reset load state whenever the src identity changes, then reconcile with
    // the element. A cached image can finish loading (fire `load`) before this
    // passive effect runs; without the reconcile we'd clobber realLoaded back
    // to false and onLoad never refires — leaving the avatar stuck showing
    // only the background color. Checking complete/naturalWidth recovers that.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset + reconcile image-load tracking when src changes (not a cascading render)
    setRealLoaded(false)
    setRealFailed(false)
    const el = realImgRef.current
    if (!el || !safeSrc) return
    if (el.complete) {
      if (el.naturalWidth > 0) setRealLoaded(true)
      else setRealFailed(true)
    }
  }, [safeSrc])

  const showReal = safeSrc && !realFailed && realLoaded
  // Generated SVG shows when there is no real src (never-saved avatar), OR as
  // a fallback when the real src fails to load. While a real src is loading
  // we render nothing visible (transparent) so there's no "default -> real"
  // swap on refresh — the real image fades in once it arrives. When `loading`
  // is set (caller knows data is still fetching), show nothing at all.
  const showGenerated = !loading && (!safeSrc || realFailed)

  return (
    <span
      data-slot="avatar"
      data-size={size}
      className={cn(
        'group/avatar relative inline-flex shrink-0 select-none items-center justify-center overflow-hidden rounded-full',
        SIZE_CLASS[size] ?? 'size-8',
        className,
      )}
      // Background color is always present (generated or real) so the avatar
      // keeps its identity color even after a real photo is saved — the photo
      // is object-cover on top, transparent edges show the color through.
      style={{ backgroundColor: avatarColor(effectiveSeed) }}
    >
      {showGenerated && (
        // eslint-disable-next-line @next/next/no-img-element -- data URI, no Next image optimization
        <img src={svgDataUri} alt="" className="size-full object-cover" />
      )}
      {/* Real image; fades in once loaded. onError drops to the generated SVG. */}
      {safeSrc && !realFailed && !loading && (
        // eslint-disable-next-line @next/next/no-img-element -- user/SSO avatar proxy URL
        <img
          ref={realImgRef}
          src={safeSrc}
          alt={name ?? ''}
          onLoad={() => setRealLoaded(true)}
          onError={() => setRealFailed(true)}
          className={cn(
            'absolute inset-0 size-full object-cover transition-opacity',
            showReal ? 'opacity-100' : 'opacity-0',
          )}
        />
      )}
    </span>
  )
}
