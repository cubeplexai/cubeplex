'use client'

import { useState } from 'react'
import { Plus } from 'lucide-react'
import type { Provider, ProviderCreate, ApiClient, TestResult } from '@cubebox/core'
import { ProviderLogo } from './ProviderLogo'
import { ProviderFormDialog } from './ProviderFormDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface ProviderListProps {
  providers: Provider[]
  selectedId: string | null
  loading: boolean
  onSelect: (id: string | null) => void
  onCreateProvider: (client: ApiClient, body: ProviderCreate) => Promise<Provider>
  onTestConnection: (
    client: ApiClient,
    body: { provider_type: string; base_url: string; api_key?: string | null; auth_type: string },
  ) => Promise<TestResult>
  client: ApiClient
}

export function ProviderList({
  providers,
  selectedId,
  loading,
  onSelect,
  onCreateProvider,
  onTestConnection,
  client,
}: ProviderListProps) {
  const [showCreate, setShowCreate] = useState(false)

  function getBadge(
    provider: Provider,
  ): { label: string; variant: 'default' | 'secondary' | 'outline' } | null {
    if (provider.is_system) return { label: '系统', variant: 'secondary' }
    if (provider.provider_type === 'openai' || provider.provider_type === 'anthropic') {
      return { label: 'API', variant: 'outline' }
    }
    if (provider.auth_type === 'oauth') return { label: 'OAuth', variant: 'outline' }
    return null
  }

  async function handleCreate(body: ProviderCreate): Promise<void> {
    const provider = await onCreateProvider(client, body)
    onSelect(provider.id)
    setShowCreate(false)
  }

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center text-xs text-muted-foreground px-4">
        加载中...
      </div>
    )
  }

  if (providers.length === 0 && !loading) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
        <p className="text-sm text-muted-foreground">暂无 provider</p>
        <p className="text-xs text-muted-foreground/70">点击下方按钮添加 LLM provider</p>
      </div>
    )
  }

  return (
    <>
      <div className="flex flex-1 flex-col overflow-y-auto">
        <ul className="flex flex-col gap-1 p-2">
          {providers.map((provider) => {
            const badge = getBadge(provider)
            return (
              <li key={provider.id}>
                <button
                  type="button"
                  onClick={() => onSelect(provider.id)}
                  aria-current={provider.id === selectedId ? 'true' : undefined}
                  className={cn(
                    'flex w-full items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left text-sm transition-all',
                    provider.id === selectedId
                      ? 'border-primary/40 bg-primary/5 shadow-sm'
                      : 'border-transparent hover:bg-accent/40',
                  )}
                >
                  <ProviderLogo name={provider.name} logoUrl={provider.logo_url} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{provider.name}</div>
                    <div className="text-[11px] text-muted-foreground">
                      {provider.model_count} 模型
                    </div>
                  </div>
                  {badge && (
                    <Badge variant={badge.variant} className="ml-auto shrink-0 text-[10px]">
                      {badge.label}
                    </Badge>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      </div>

      <div className="border-t p-3">
        <Button
          variant="outline"
          size="sm"
          className="w-full gap-1.5"
          onClick={() => setShowCreate(true)}
        >
          <Plus className="size-3.5" />
          添加
        </Button>
      </div>

      <ProviderFormDialog
        open={showCreate}
        onOpenChange={setShowCreate}
        provider={null}
        client={client}
        onTestConnection={onTestConnection}
        onSave={(body) => handleCreate(body as ProviderCreate)}
      />
    </>
  )
}
