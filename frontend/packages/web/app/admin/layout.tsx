'use client'

import { useEffect, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, useAuthStore } from '@cubeplex/core'
import { useTranslations } from 'next-intl'

import { AdminSubNav } from '@/components/admin/AdminSubNav'
import { AdminTopBar } from '@/components/admin/AdminTopBar'
import { useAdminAccess } from '@/hooks/useAdminAccess'

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const t = useTranslations('adminLayout')
  const client = useMemo(() => createApiClient(''), [])
  const { isAdmin, orgName, loading, error } = useAdminAccess()
  const router = useRouter()

  // Load current user into authStore so AvatarPopover can render the email.
  useEffect(() => {
    useAuthStore.getState().loadMe(client)
  }, [client])

  useEffect(() => {
    if (loading) return
    if (error) {
      router.replace('/login?next=/admin')
      return
    }
    if (!isAdmin) {
      router.replace('/')
    }
  }, [loading, isAdmin, error, router])

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
        {t('loading')}
      </div>
    )
  }
  if (!isAdmin) return null

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <AdminTopBar orgName={orgName} />
      <div className="flex flex-1 overflow-hidden">
        <AdminSubNav />
        <main className="flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
