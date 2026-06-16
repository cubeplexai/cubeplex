'use client'

import { useCallback, useEffect, useState } from 'react'
import { createApiClient, useWorkspaceSettingsStore, useWorkspaceStore } from '@cubebox/core'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { SETTINGS_CONTENT_WIDTH, SectionHeader } from '@/components/shared/SectionHeader'
import { cn } from '@/lib/utils'
import { WorkspaceDangerZone } from './WorkspaceDangerZone'

interface PersonaEditorProps {
  wsId: string
}

export function PersonaEditor({ wsId }: PersonaEditorProps) {
  const t = useTranslations('wsSettings')
  const tGeneral = useTranslations('wsSettings.general')
  const tPersona = useTranslations('wsSettings.persona')
  const { agentConfig, loading, loadAll, savePersona } = useWorkspaceSettingsStore()
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  const workspaceName = useWorkspaceStore(
    (s) => s.workspaces.find((w) => w.id === wsId)?.name ?? '',
  )
  const renameWorkspace = useWorkspaceStore((s) => s.rename)
  const [nameDraft, setNameDraft] = useState('')
  const [nameSaving, setNameSaving] = useState(false)

  useEffect(() => {
    setNameDraft(workspaceName)
  }, [workspaceName])

  const client = useCallback(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  // Plain client without workspace-scoped header for workspace-level PATCH
  const plainClient = useCallback(() => createApiClient(''), [])

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

  const handleNameSave = async (): Promise<void> => {
    const trimmed = nameDraft.trim()
    if (!trimmed || trimmed === workspaceName) return
    setNameSaving(true)
    try {
      await renameWorkspace(plainClient(), wsId, trimmed)
      toast.success(t('workspaceNameSaved'))
    } finally {
      setNameSaving(false)
    }
  }

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <SectionHeader
        title={tGeneral('title')}
        description={tGeneral('description')}
        contained={SETTINGS_CONTENT_WIDTH}
      />

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className={cn('flex w-full flex-col gap-8', SETTINGS_CONTENT_WIDTH)}>
          <div className="flex flex-col gap-2">
            <Label htmlFor="ws-name" className="text-sm font-medium">
              {t('workspaceName')}
            </Label>
            <div className="flex items-center gap-2">
              <Input
                id="ws-name"
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void handleNameSave()
                }}
                className="max-w-sm"
                disabled={nameSaving}
              />
              <Button
                size="sm"
                onClick={() => void handleNameSave()}
                disabled={nameSaving || !nameDraft.trim() || nameDraft.trim() === workspaceName}
              >
                {nameSaving ? t('workspaceNameSaving') : t('workspaceNameSave')}
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-3">
            <div>
              <h3 className="text-sm font-medium">{tPersona('title')}</h3>
              <p className="mt-0.5 text-xs text-muted-foreground">{tPersona('description')}</p>
            </div>
            {loading && !agentConfig ? (
              <p className="text-sm text-muted-foreground">{tPersona('loading')}</p>
            ) : (
              <>
                <Textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder={tPersona('placeholder')}
                  className="min-h-[260px] resize-y font-mono text-sm leading-relaxed"
                />
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {tPersona('charCount', { count: draft.length })}
                  </span>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={handleReset} disabled={saving}>
                      {tPersona('reset')}
                    </Button>
                    <Button size="sm" onClick={() => void handleSave()} disabled={saving}>
                      {saving ? tPersona('saving') : tPersona('save')}
                    </Button>
                  </div>
                </div>
              </>
            )}
          </div>

          <WorkspaceDangerZone wsId={wsId} />
        </div>
      </div>
    </div>
  )
}
