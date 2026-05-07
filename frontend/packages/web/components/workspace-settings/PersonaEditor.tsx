'use client'

import { useCallback, useEffect, useState } from 'react'
import { createApiClient, useWorkspaceSettingsStore } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

interface PersonaEditorProps {
  wsId: string
}

export function PersonaEditor({ wsId }: PersonaEditorProps) {
  const t = useTranslations('wsSettings.persona')
  const { agentConfig, loading, loadAll, savePersona } = useWorkspaceSettingsStore()
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  const client = useCallback(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!agentConfig) {
      loadAll(client())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsId])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (agentConfig) setDraft(agentConfig.system_prompt)
  }, [agentConfig])

  const handleSave = async (): Promise<void> => {
    setSaving(true)
    try {
      await savePersona(client(), draft)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = (): void => {
    setDraft(agentConfig?.system_prompt ?? '')
  }

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('description')}</p>
      </header>

      <div className="flex flex-1 overflow-y-auto">
        <div className="flex w-full max-w-3xl flex-col gap-4 p-6">
          {loading && !agentConfig ? (
            <p className="text-sm text-muted-foreground">{t('loading')}</p>
          ) : (
            <>
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder={t('placeholder')}
                className="min-h-[260px] resize-y font-mono text-sm leading-relaxed"
              />
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  {t('charCount', { count: draft.length })}
                </span>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={handleReset} disabled={saving}>
                    {t('reset')}
                  </Button>
                  <Button size="sm" onClick={() => void handleSave()} disabled={saving}>
                    {saving ? t('saving') : t('save')}
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
