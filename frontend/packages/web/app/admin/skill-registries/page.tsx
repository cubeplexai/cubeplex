'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { RegistryCard } from '@/components/admin/skill-registries/RegistryCard'
import { RegistryDetailPanel } from '@/components/admin/skill-registries/RegistryDetailPanel'
import { AddRegistryForm } from '@/components/admin/skill-registries/AddRegistryForm'
import { useAdminSkillRegistries } from '@/hooks/useAdminSkillRegistries'

export default function SkillRegistriesPage() {
  const t = useTranslations('adminSkillRegistries')
  const { registries, loading, mutating, error, create, patch, remove } = useAdminSkillRegistries()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    document.title = t('pageTitle')
  }, [t])

  const selected = registries.find((r) => r.id === selectedId) ?? null

  async function handleCreate(body: Parameters<typeof create>[0]) {
    const created = await create(body)
    if (created) {
      setAdding(false)
      setSelectedId(created.id)
    }
    return !!created
  }

  async function handleDelete(id: string) {
    const ok = await remove(id)
    if (ok && selectedId === id) setSelectedId(null)
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
      </header>

      <div className="flex items-center gap-2 border-b border-border/70 px-4 py-3">
        <Button
          size="sm"
          onClick={() => {
            setAdding(true)
            setSelectedId(null)
          }}
        >
          <Plus className="size-3.5" />
          {t('addRegistry')}
        </Button>
      </div>

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label={t('listAria')}
          className="w-[300px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          {loading ? (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t('loading')}</p>
          ) : registries.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
              <p className="text-sm text-muted-foreground">{t('empty')}</p>
              <p className="text-xs text-muted-foreground/70">{t('emptyHint')}</p>
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5 p-3">
              {registries.map((r) => (
                <li key={r.id}>
                  <RegistryCard
                    registry={r}
                    active={r.id === selectedId && !adding}
                    onClick={() => {
                      setSelectedId(r.id)
                      setAdding(false)
                    }}
                  />
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          {adding ? (
            <AddRegistryForm
              onSubmit={handleCreate}
              onCancel={() => setAdding(false)}
              mutating={mutating}
              error={error}
            />
          ) : selected ? (
            <RegistryDetailPanel
              registry={selected}
              onPatch={patch}
              onDelete={handleDelete}
              mutating={mutating}
              error={error}
            />
          ) : (
            <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
              {t('selectOrAdd')}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
