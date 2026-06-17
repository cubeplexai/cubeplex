'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useTranslations } from 'next-intl'
import {
  Activity,
  BarChart3,
  Box,
  Cpu,
  Database,
  Globe,
  KeyRound,
  Shield,
  Layers,
  MessageSquare,
  Plug,
  Puzzle,
  Settings,
  Sparkles,
  Users,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Separator } from '@/components/ui/separator'
import { AvatarPopover } from '@/components/sidebar/AvatarPopover'
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
        'relative flex items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors duration-fast',
        active
          ? 'bg-accent text-foreground font-medium'
          : 'text-muted-foreground hover:text-foreground hover:bg-accent',
      )}
    >
      {active && (
        <span className="absolute left-0 top-[22%] bottom-[22%] w-0.5 bg-primary rounded-r" />
      )}
      <Icon className={cn('size-3.5 shrink-0', active ? 'text-primary' : 'text-faint')} />
      <span className="truncate">{label}</span>
    </Link>
  )
}

export function AdminSubNav() {
  const t = useTranslations('adminNav')
  const tLayout = useTranslations('adminLayout')
  const pathname = usePathname() ?? ''
  const { extensions } = useAdminExtensions()

  const NATIVE_ITEMS: NavDef[] = [
    { href: '/admin/settings', label: t('settings'), icon: Settings },
    { href: '/admin/members', label: t('members'), icon: Users },
    { href: '/admin/authentication', label: t('authentication'), icon: Shield },
    { href: '/admin/models', label: t('models'), icon: Cpu },
    { href: '/admin/presets', label: t('modelPresets'), icon: Layers },
    { href: '/admin/web-tools', label: t('webTools'), icon: Globe },
    { href: '/admin/skills', label: t('skills'), icon: Sparkles },
    { href: '/admin/skill-registries', label: t('skillRegistries'), icon: Database },
    { href: '/admin/mcp', label: t('mcp'), icon: Plug },
    { href: '/admin/im', label: t('im'), icon: MessageSquare },
    { href: '/admin/sandbox', label: t('sandbox'), icon: Box },
    { href: '/admin/sandbox-env', label: t('sandboxEnv'), icon: KeyRound },
    { href: '/admin/insights', label: t('insights'), icon: BarChart3 },
    { href: '/admin/traces', label: t('traces'), icon: Activity },
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
      aria-label={tLayout('subNavAria')}
      className="w-56 border-r border-border bg-card flex flex-col shrink-0"
    >
      <ul className="space-y-0.5 flex-1 overflow-y-auto p-2">
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
              <p className="px-2 py-1 text-2xs font-medium uppercase tracking-wider text-faint">
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
      <div className="border-t border-border p-2 shrink-0">
        <AvatarPopover />
      </div>
    </nav>
  )
}
