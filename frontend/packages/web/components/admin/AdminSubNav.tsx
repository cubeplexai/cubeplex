'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Box, Cpu, Globe, Plug, Puzzle, Sparkles } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Separator } from '@/components/ui/separator'
import { useAdminExtensions } from '@/hooks/useAdminExtensions'
import { cn } from '@/lib/utils'

type NavDef = {
  href: string
  label: string
  icon: LucideIcon
}

function NavItem({ href, label, icon: Icon, active }: NavDef & { active: boolean }) {
  return (
    <Link
      href={href}
      className={cn(
        'relative flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] transition-colors',
        active
          ? 'bg-primary/10 text-foreground font-medium'
          : 'text-muted-foreground hover:text-foreground hover:bg-accent/60',
      )}
    >
      {active && (
        <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-4 bg-primary rounded-r-full" />
      )}
      <Icon
        className={cn('size-3.5 shrink-0', active ? 'text-primary' : 'text-muted-foreground/70')}
      />
      <span className="truncate">{label}</span>
    </Link>
  )
}

export function AdminSubNav() {
  const t = useTranslations('adminNav')
  const pathname = usePathname() ?? ''
  const { extensions } = useAdminExtensions()

  const NATIVE_ITEMS: NavDef[] = [
    { href: '/admin/models', label: t('models'), icon: Cpu },
    { href: '/admin/web-tools', label: t('webTools'), icon: Globe },
    { href: '/admin/skills', label: t('skills'), icon: Sparkles },
    { href: '/admin/mcp', label: t('mcp'), icon: Plug },
    { href: '/admin/sandbox', label: t('sandbox'), icon: Box },
  ]

  const extItems: NavDef[] = extensions.flatMap((ext) =>
    ext.nav_items.map((item) => ({
      href: `/admin/ext/${ext.plugin}/${item.url_path}`,
      label: item.label,
      icon: Puzzle,
    })),
  )

  return (
    <nav
      aria-label="Admin sub-nav"
      className="w-56 border-r border-border/70 bg-card/40 flex flex-col p-2 overflow-y-auto"
    >
      <ul className="space-y-0.5">
        {NATIVE_ITEMS.map((item) => {
          const active = pathname === item.href || pathname.startsWith(item.href + '/')
          return (
            <li key={item.href}>
              <NavItem {...item} active={active} />
            </li>
          )
        })}

        {extItems.length > 0 && (
          <>
            <li className="py-2">
              <Separator />
            </li>
            <li>
              <p className="px-2 py-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
                {t('extensions')}
              </p>
            </li>
            {extItems.map((item) => {
              const active = pathname === item.href || pathname.startsWith(item.href + '/')
              return (
                <li key={item.href}>
                  <NavItem {...item} active={active} />
                </li>
              )
            })}
          </>
        )}
      </ul>
    </nav>
  )
}
