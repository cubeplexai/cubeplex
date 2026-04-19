'use client'

import Link from 'next/link'
import { Box } from 'lucide-react'
import { WorkspaceSwitcher } from '@/components/workspace/WorkspaceSwitcher'
import { AvatarMenu } from '@/components/layout/AvatarMenu'

export function AppTopBar() {
  return (
    <header className="border-b border-border bg-background">
      <div className="flex h-12 items-center gap-3 px-4">
        <Link href="/" className="flex items-center gap-2">
          <Box className="size-5" />
          <span className="text-sm font-semibold">cubebox</span>
        </Link>
        <div className="ml-2">
          <WorkspaceSwitcher />
        </div>
        <div className="ml-auto">
          <AvatarMenu />
        </div>
      </div>
    </header>
  )
}
