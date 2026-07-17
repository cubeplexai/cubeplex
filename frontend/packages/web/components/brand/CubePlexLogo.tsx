import type { HTMLAttributes, SVGProps } from 'react'
import { cn } from '@/lib/utils'

type CubePlexMarkProps = SVGProps<SVGSVGElement>

export function CubePlexMark({ className, ...props }: CubePlexMarkProps): React.ReactElement {
  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      focusable="false"
      className={cn('shrink-0', className)}
      {...props}
    >
      <path
        d="M32 7 55 20 32 33 9 20 32 7Z"
        stroke="var(--brand-mark-primary)"
        strokeWidth="4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="m10 32 22 12.5L54 32M10 43.5 32 56l22-12.5"
        stroke="currentColor"
        strokeWidth="4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

type CubePlexLogoProps = HTMLAttributes<HTMLDivElement> & {
  markClassName?: string
  wordmarkClassName?: string
}

export function CubePlexLogo({
  className,
  markClassName,
  wordmarkClassName,
  ...props
}: CubePlexLogoProps): React.ReactElement {
  return (
    <div className={cn('inline-flex items-center gap-2.5 text-foreground', className)} {...props}>
      <CubePlexMark aria-hidden="true" className={markClassName} />
      <span
        className={cn(
          'font-semibold tracking-[-0.045em] [font-family:var(--font-geist-sans)]',
          wordmarkClassName,
        )}
      >
        CubePlex
      </span>
    </div>
  )
}
