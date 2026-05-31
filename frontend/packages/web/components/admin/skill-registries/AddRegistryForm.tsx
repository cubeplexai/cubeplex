'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { CreateRegistryBody } from '@/hooks/useAdminSkillRegistries'

const TRUST_TIERS = ['official', 'community', 'untrusted'] as const

interface AddRegistryFormProps {
  onSubmit: (body: CreateRegistryBody) => Promise<boolean>
  onCancel: () => void
  mutating: boolean
  error: string | null
}

export function AddRegistryForm({ onSubmit, onCancel, mutating, error }: AddRegistryFormProps) {
  const t = useTranslations('adminSkillRegistries')
  const [kind, setKind] = useState<'skills-sh' | 'clawhub' | 'remote'>('skills-sh')
  const [name, setName] = useState('skills.sh')
  const [baseUrl, setBaseUrl] = useState('')
  const [trustTier, setTrustTier] = useState<string>('community')

  function handleKindChange(next: 'skills-sh' | 'clawhub' | 'remote') {
    setKind(next)
    if (next === 'skills-sh') {
      setName('skills.sh')
      setTrustTier('community')
    } else if (next === 'clawhub') {
      setName('Clawhub')
      setTrustTier('community')
    } else {
      setName('')
      setTrustTier('untrusted')
    }
    setBaseUrl('')
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const body: CreateRegistryBody = {
      name: name.trim(),
      kind,
      trust_tier: trustTier,
      ...(kind === 'remote' ? { base_url: baseUrl.trim() } : {}),
    }
    await onSubmit(body)
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-1 flex-col gap-6 p-6">
      <h3 className="text-base font-semibold">{t('addTitle')}</h3>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {error}
        </div>
      )}

      {/* Kind selector */}
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium">{t('kind')}</label>
        <div className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/30 p-0.5 self-start">
          {(['skills-sh', 'clawhub', 'remote'] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => handleKindChange(k)}
              className={cn(
                'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                k === kind
                  ? 'bg-background text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {k === 'skills-sh' ? 'skills.sh' : k === 'clawhub' ? 'Clawhub' : t('customRegistry')}
            </button>
          ))}
        </div>
      </div>

      {/* Name */}
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium">{t('name')}</label>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('namePlaceholder')}
          required
          className="max-w-sm"
        />
      </div>

      {/* Base URL (custom only) */}
      {kind === 'remote' && (
        <div className="flex flex-col gap-2">
          <label className="text-sm font-medium">{t('registryUrl')}</label>
          <Input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://registry.example.com"
            required
            type="url"
            className="max-w-sm"
          />
          <p className="text-xs text-muted-foreground">{t('registryUrlHint')}</p>
        </div>
      )}

      {/* Trust tier */}
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium">{t('trustTier')}</label>
        <div className="inline-flex items-center gap-0.5 rounded-lg border border-border bg-muted/30 p-0.5 self-start">
          {TRUST_TIERS.map((tier) => (
            <button
              key={tier}
              type="button"
              onClick={() => setTrustTier(tier)}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors capitalize',
                tier === trustTier
                  ? 'bg-background text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {tier}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2 pt-2">
        <Button type="submit" size="sm" disabled={mutating || !name.trim()}>
          {mutating ? t('adding') : t('add')}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          {t('cancel')}
        </Button>
      </div>
    </form>
  )
}
