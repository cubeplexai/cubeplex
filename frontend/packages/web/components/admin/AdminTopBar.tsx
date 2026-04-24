'use client'

import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Box } from 'lucide-react'
import { AdminAvatarMenu } from './AdminAvatarMenu'

interface AdminTopBarProps {
  orgName: string
}

function handleBackToApp() {
  if (typeof window === 'undefined') return
  if (window.opener) {
    window.close()
  } else {
    window.location.href = '/'
  }
}

export function AdminTopBar({ orgName }: AdminTopBarProps) {
  return (
    <header className="flex items-center gap-3 border-b border-border bg-card/80 backdrop-blur supports-[backdrop-filter]:bg-card/60 px-4 h-14 shrink-0">
      <div className="flex items-center gap-2">
        <div className="w-6 h-6 rounded-md bg-primary flex items-center justify-center shrink-0 shadow-sm">
          <Box className="size-3.5 text-primary-foreground" strokeWidth={2.5} />
        </div>
        <span className="text-sm font-semibold tracking-tight">cubebox</span>
      </div>
      <Separator orientation="vertical" className="h-5" />
      <h1 className="text-sm font-medium">管理后台</h1>
      {orgName && (
        <span className="text-sm text-muted-foreground/70 before:content-['·'] before:mr-2 before:text-muted-foreground/40">
          {orgName}
        </span>
      )}

      <div className="ml-auto flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={handleBackToApp}>
          回应用
        </Button>
        <AdminAvatarMenu />
      </div>
    </header>
  )
}
