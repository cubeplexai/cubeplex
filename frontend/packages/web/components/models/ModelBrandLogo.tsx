import { Cpu } from 'lucide-react'

import { BRAND_ICONS } from '@/lib/models/brand-icons'
import { cn } from '@/lib/utils'

interface ModelBrandLogoProps {
  /** Catalog / heuristic brand id (e.g. "anthropic"), or null for default icon. */
  brand: string | null
  /** Accessible name (tier or model label). */
  label: string
  size?: 'sm' | 'lg'
  className?: string
}

/**
 * Model-family brand glyph for the chat model picker.
 * Falls back to a generic CPU icon when brand is unknown — never letter monogram.
 */
export function ModelBrandLogo({
  brand,
  label,
  size = 'sm',
  className,
}: ModelBrandLogoProps): React.ReactElement {
  const boxClass = size === 'sm' ? 'size-5' : 'size-8'
  const iconPx = size === 'sm' ? 14 : 20
  const BrandIcon = brand ? BRAND_ICONS[brand] : undefined

  if (BrandIcon) {
    return (
      <span
        className={cn(
          'flex shrink-0 items-center justify-center rounded-full bg-muted',
          boxClass,
          className,
        )}
      >
        <BrandIcon size={iconPx} aria-label={label} />
      </span>
    )
  }

  return (
    <span
      className={cn(
        'flex shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground',
        boxClass,
        className,
      )}
      aria-hidden={false}
    >
      <Cpu aria-label={label} className={size === 'sm' ? 'size-3.5' : 'size-5'} />
    </span>
  )
}
