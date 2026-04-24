'use client'

import useSWR from 'swr'

export type AdminNavItem = {
  id: string
  label: string
  icon: string | null
  section: string
  order: number
  url_path: string
}

export type AdminExtensionEntry = {
  plugin: string
  nav_items: AdminNavItem[]
  iframe_base_url: string
}

async function fetcher(url: string): Promise<AdminExtensionEntry[]> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`manifest fetch failed: ${res.status}`)
  return res.json() as Promise<AdminExtensionEntry[]>
}

export function useAdminExtensions() {
  const { data, error, isLoading } = useSWR<AdminExtensionEntry[]>(
    '/api/v1/admin/_extensions/manifest',
    fetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  return {
    extensions: data ?? [],
    loading: isLoading,
    error: error as Error | undefined,
  }
}
