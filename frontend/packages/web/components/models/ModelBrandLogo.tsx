'use client'

import { Cpu } from 'lucide-react'
import dynamic from 'next/dynamic'

import { cn } from '@/lib/utils'

interface ModelBrandLogoProps {
  /** Catalog / heuristic brand id (e.g. "anthropic"), or null for default icon. */
  brand: string | null
  /** Accessible name (tier or model label). */
  label: string
  size?: 'sm' | 'lg'
  className?: string
}

const DynamicBrandIcon = dynamic(
  () =>
    import('@/lib/models/brand-icons').then((m) => {
      return function BrandIconWrapper({
        brand,
        size,
        label,
      }: {
        brand: string
        size: number
        label: string
      }) {
        const Icon = m.BRAND_ICONS[brand]
        if (!Icon) return <Cpu aria-label={label} className={size === 14 ? 'size-3.5' : 'size-5'} />
        return <Icon size={size} aria-label={label} />
      }
    }),
  {
    ssr: false,
    loading: () => <Cpu aria-hidden className="size-3.5 text-muted-foreground" />,
  },
)

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

  if (brand) {
    return (
      <span
        className={cn(
          'flex shrink-0 items-center justify-center rounded-full bg-muted',
          boxClass,
          className,
        )}
      >
        <DynamicBrandIcon brand={brand} size={iconPx} label={label} />
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
