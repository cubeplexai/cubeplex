import Image from 'next/image'
import { cn } from '@/lib/utils'

const COLORS = [
  { bg: 'bg-blue-100', text: 'text-blue-600', dark: 'dark:bg-blue-900/40 dark:text-blue-300' },
  { bg: 'bg-green-100', text: 'text-green-600', dark: 'dark:bg-green-900/40 dark:text-green-300' },
  {
    bg: 'bg-purple-100',
    text: 'text-purple-600',
    dark: 'dark:bg-purple-900/40 dark:text-purple-300',
  },
  { bg: 'bg-amber-100', text: 'text-amber-600', dark: 'dark:bg-amber-900/40 dark:text-amber-300' },
  { bg: 'bg-rose-100', text: 'text-rose-600', dark: 'dark:bg-rose-900/40 dark:text-rose-300' },
  { bg: 'bg-cyan-100', text: 'text-cyan-600', dark: 'dark:bg-cyan-900/40 dark:text-cyan-300' },
]

function hashName(name: string): number {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return hash
}

interface ProviderLogoProps {
  name: string
  logoUrl: string | null
  size?: 'sm' | 'lg'
}

export function ProviderLogo({ name, logoUrl, size = 'sm' }: ProviderLogoProps) {
  const colorIndex = hashName(name) % COLORS.length
  const color = COLORS[colorIndex]

  if (logoUrl) {
    return (
      <div
        className={cn(
          'relative shrink-0 overflow-hidden rounded-full',
          size === 'sm' ? 'size-6' : 'size-10',
        )}
      >
        <Image src={logoUrl} alt={name} fill className="object-cover" unoptimized />
      </div>
    )
  }

  return (
    <div
      className={cn(
        'flex shrink-0 items-center justify-center rounded-full font-semibold',
        color.bg,
        color.text,
        color.dark,
        size === 'sm' ? 'size-6 text-xs' : 'size-10 text-sm',
      )}
    >
      {name.charAt(0).toUpperCase()}
    </div>
  )
}
