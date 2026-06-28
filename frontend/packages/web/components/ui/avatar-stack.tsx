'use client'

import { Avatar, type AvatarProps } from '@/components/ui/avatar-resolved'

export interface AvatarStackItem {
  src?: string | null
  seed?: string | null
  name?: string | null
  userId?: string
}

/** Maps shadcn size presets to their pixel value. */
const SIZE_PRESET_MAP: Record<NonNullable<AvatarProps['size']>, number> = {
  sm: 24,
  default: 32,
  lg: 40,
  xl: 64,
}

function nearestPreset(pixels: number): NonNullable<AvatarProps['size']> {
  const entries = Object.entries(SIZE_PRESET_MAP) as [NonNullable<AvatarProps['size']>, number][]
  let best = entries[0]!
  for (const entry of entries) {
    if (Math.abs(entry[1] - pixels) < Math.abs(best[1] - pixels)) {
      best = entry
    }
  }
  return best[0]
}

export function AvatarStack({
  items,
  max = 5,
  size = 24,
  style,
}: {
  items: AvatarStackItem[]
  max?: number
  size?: number
  style?: AvatarProps['style']
}) {
  const shown = items.slice(0, max)
  const overflow = items.length - shown.length
  const avatarSize = nearestPreset(size)
  return (
    <div className="flex items-center">
      {shown.map((it, i) => (
        <div
          key={i}
          className="rounded-full ring-2 ring-background"
          style={{ marginLeft: i === 0 ? 0 : -size / 3 }}
        >
          <Avatar
            src={it.src}
            seed={it.seed}
            name={it.name}
            userId={it.userId}
            style={style}
            size={avatarSize}
          />
        </div>
      ))}
      {overflow > 0 && (
        <div
          className="inline-flex items-center justify-center rounded-full bg-muted text-muted-foreground"
          style={{
            width: size,
            height: size,
            marginLeft: -size / 3,
            fontSize: size * 0.4,
          }}
        >
          +{overflow}
        </div>
      )}
    </div>
  )
}
