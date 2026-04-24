'use client'

import { use } from 'react'
import { useAdminExtensions } from '@/hooks/useAdminExtensions'

interface ExtensionPageProps {
  params: Promise<{ plugin: string; path?: string[] }>
}

export default function ExtensionPage({ params }: ExtensionPageProps) {
  const { plugin, path } = use(params)
  const { extensions, loading } = useAdminExtensions()

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading extension…
      </div>
    )
  }

  const ext = extensions.find((e) => e.plugin === plugin)
  if (!ext) {
    return (
      <div className="max-w-2xl mx-auto mt-16 px-6">
        <h2 className="text-2xl font-semibold tracking-tight mb-2">未知扩展</h2>
        <p className="text-muted-foreground leading-relaxed">
          找不到名为 <code className="rounded bg-muted px-1.5 py-0.5 text-sm">{plugin}</code> 的插件
          —— 它可能未安装或已禁用。
        </p>
      </div>
    )
  }

  const subPath = (path ?? []).join('/')
  const iframeUrl = `${ext.iframe_base_url}${subPath}`

  return (
    <iframe
      src={iframeUrl}
      className="h-full w-full border-0"
      sandbox="allow-scripts allow-forms allow-same-origin"
      title={`Extension: ${plugin}`}
    />
  )
}
