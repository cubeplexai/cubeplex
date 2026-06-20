'use client'

import { useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useTranslations } from 'next-intl'
import {
  Activity,
  BarChart3,
  Box,
  ChevronRight,
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

type NavLeaf = {
  href: string
  label: string
  icon: LucideIcon
}

type NavGroup = {
  key: string
  label: string
  icon: LucideIcon
  children: NavLeaf[]
}

type NavEntry = NavLeaf | NavGroup

function isGroup(entry: NavEntry): entry is NavGroup {
  return 'children' in entry
}

function isActiveHref(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(href + '/')
}

function NavItem({ href, label, icon: Icon, active }: NavLeaf & { active: boolean }) {
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

function NavGroupItem({
  group,
  pathname,
  open,
  onToggle,
}: {
  group: NavGroup
  pathname: string
  open: boolean
  onToggle: () => void
}) {
  const Icon = group.icon
  const hasActiveChild = group.children.some((c) => isActiveHref(pathname, c.href))

  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className={cn(
          'flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors duration-fast',
          hasActiveChild && !open
            ? 'text-foreground font-medium'
            : 'text-muted-foreground hover:text-foreground hover:bg-accent',
        )}
      >
        <Icon className={cn('size-3.5 shrink-0', hasActiveChild ? 'text-primary' : 'text-faint')} />
        <span className="flex-1 truncate text-left">{group.label}</span>
        <ChevronRight
          className={cn('size-3.5 shrink-0 text-faint transition-transform', open && 'rotate-90')}
        />
      </button>
      {open && (
        <ul className="ml-[15px] mt-0.5 space-y-0.5 border-l border-border/60 pl-2">
          {group.children.map((child) => (
            <li key={child.href}>
              <NavItem {...child} active={isActiveHref(pathname, child.href)} />
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export function AdminSubNav() {
  const t = useTranslations('adminNav')
  const tLayout = useTranslations('adminLayout')
  const pathname = usePathname() ?? ''
  const { extensions } = useAdminExtensions()
  const [openOverride, setOpenOverride] = useState<Record<string, boolean>>({})

  const ENTRIES: NavEntry[] = [
    { href: '/admin/settings', label: t('settings'), icon: Settings },
    { href: '/admin/members', label: t('members'), icon: Users },
    { href: '/admin/authentication', label: t('authentication'), icon: Shield },
    {
      key: 'models',
      label: t('groupModels'),
      icon: Cpu,
      children: [
        { href: '/admin/models', label: t('models'), icon: Cpu },
        { href: '/admin/presets', label: t('modelPresets'), icon: Layers },
      ],
    },
    { href: '/admin/web-tools', label: t('webTools'), icon: Globe },
    {
      key: 'skills',
      label: t('groupSkills'),
      icon: Sparkles,
      children: [
        { href: '/admin/skills', label: t('skills'), icon: Sparkles },
        { href: '/admin/skill-registries', label: t('skillRegistries'), icon: Database },
      ],
    },
    { href: '/admin/mcp', label: t('mcp'), icon: Plug },
    { href: '/admin/im', label: t('im'), icon: MessageSquare },
    {
      key: 'sandbox',
      label: t('groupSandbox'),
      icon: Box,
      children: [
        { href: '/admin/sandbox', label: t('sandbox'), icon: Box },
        { href: '/admin/sandbox-env', label: t('sandboxEnv'), icon: KeyRound },
      ],
    },
    { href: '/admin/insights', label: t('insights'), icon: BarChart3 },
    { href: '/admin/traces', label: t('traces'), icon: Activity },
  ]

  // A group defaults to open when it holds the active route; an explicit
  // toggle overrides that default.
  const isGroupOpen = (group: NavGroup): boolean =>
    openOverride[group.key] ?? group.children.some((c) => isActiveHref(pathname, c.href))

  const toggleGroup = (group: NavGroup): void =>
    setOpenOverride((prev) => ({ ...prev, [group.key]: !isGroupOpen(group) }))

  const extItems: NavLeaf[] = extensions.flatMap((ext) =>
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
        {ENTRIES.map((entry) =>
          isGroup(entry) ? (
            <NavGroupItem
              key={entry.key}
              group={entry}
              pathname={pathname}
              open={isGroupOpen(entry)}
              onToggle={() => toggleGroup(entry)}
            />
          ) : (
            <li key={entry.href}>
              <NavItem {...entry} active={isActiveHref(pathname, entry.href)} />
            </li>
          ),
        )}

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
            {extItems.map((item) => (
              <li key={item.href}>
                <NavItem {...item} active={isActiveHref(pathname, item.href)} />
              </li>
            ))}
          </>
        )}
      </ul>
      <div className="border-t border-border p-2 shrink-0">
        <AvatarPopover />
      </div>
    </nav>
  )
}
