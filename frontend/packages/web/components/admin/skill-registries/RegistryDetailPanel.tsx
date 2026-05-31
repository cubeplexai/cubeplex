'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Database, Trash2 } from 'lucide-react'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { cn } from '@/lib/utils'
import type { SkillRegistryEntry, PatchRegistryBody } from '@/hooks/useAdminSkillRegistries'

const TRUST_TIERS = ['official', 'community', 'untrusted'] as const

interface RegistryDetailPanelProps {
  registry: SkillRegistryEntry
  onPatch: (id: string, body: PatchRegistryBody) => Promise<boolean>
  onDelete: (id: string) => Promise<boolean>
  mutating: boolean
  error: string | null
}

export function RegistryDetailPanel({
  registry,
  onPatch,
  onDelete,
  mutating,
  error,
}: RegistryDetailPanelProps) {
  const t = useTranslations('adminSkillRegistries')
  const [deleting, setDeleting] = useState(false)

  async function handleDelete() {
    setDeleting(true)
    await onDelete(registry.id)
    setDeleting(false)
  }

  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Database className="size-4 shrink-0 text-muted-foreground" />
            <h3 className="text-base font-semibold">{registry.name}</h3>
          </div>
          <span className="text-xs text-muted-foreground">
            {registry.kind === 'skills-sh'
              ? 'skills.sh'
              : registry.kind === 'clawhub'
                ? 'clawhub.ai'
                : registry.base_url}
          </span>
        </div>
        <AlertDialog>
          <AlertDialogTrigger className="inline-flex h-8 w-8 items-center justify-center rounded-md text-destructive transition-colors hover:bg-accent/60 hover:text-destructive">
            <Trash2 className="size-3.5" />
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t('deleteTitle')}</AlertDialogTitle>
              <AlertDialogDescription>
                {t('deleteDescription', { name: registry.name })}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
              <AlertDialogAction
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                onClick={handleDelete}
                disabled={deleting}
              >
                {t('delete')}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {error}
        </div>
      )}

      <dl className="flex flex-col gap-4">
        {/* Enabled toggle */}
        <div className="flex items-center justify-between">
          <dt className="text-sm font-medium">{t('enabled')}</dt>
          <dd>
            <button
              type="button"
              role="switch"
              aria-checked={registry.enabled}
              disabled={mutating}
              onClick={() => void onPatch(registry.id, { enabled: !registry.enabled })}
              className={cn(
                'relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus-visible:outline-none',
                registry.enabled ? 'bg-primary' : 'bg-muted',
                mutating && 'opacity-50 cursor-not-allowed',
              )}
            >
              <span
                className={cn(
                  'inline-block size-3.5 rounded-full bg-white shadow transition-transform',
                  registry.enabled ? 'translate-x-4' : 'translate-x-0.5',
                )}
              />
            </button>
          </dd>
        </div>

        {/* Trust tier */}
        <div className="flex items-center justify-between">
          <dt className="text-sm font-medium">{t('trustTier')}</dt>
          <dd>
            <div className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/30 p-0.5">
              {TRUST_TIERS.map((tier) => (
                <button
                  key={tier}
                  type="button"
                  disabled={mutating}
                  onClick={() => {
                    if (tier !== registry.trust_tier)
                      void onPatch(registry.id, { trust_tier: tier })
                  }}
                  className={cn(
                    'rounded-md px-2.5 py-1 text-xs font-medium transition-colors capitalize',
                    tier === registry.trust_tier
                      ? 'bg-background text-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground',
                    mutating && 'opacity-50 cursor-not-allowed',
                  )}
                >
                  {tier}
                </button>
              ))}
            </div>
          </dd>
        </div>

        {/* Base URL (custom registries only) */}
        {registry.kind !== 'skills-sh' && (
          <div className="flex items-start gap-3">
            <dt className="w-20 shrink-0 pt-0.5 text-sm font-medium">URL</dt>
            <dd className="truncate text-xs text-muted-foreground">{registry.base_url}</dd>
          </div>
        )}
      </dl>
    </div>
  )
}
