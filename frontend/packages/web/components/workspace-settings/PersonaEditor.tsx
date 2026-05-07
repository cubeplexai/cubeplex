'use client'

import { useCallback, useEffect, useState } from 'react'
import { createApiClient, useWorkspaceSettingsStore } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

interface PersonaEditorProps {
  wsId: string
}

export function PersonaEditor({ wsId }: PersonaEditorProps) {
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
        <h2 className="text-lg font-semibold tracking-tight">Persona</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Defines the agent&apos;s persona for every conversation in this workspace. Appended after
          the base system prompt.
        </p>
      </header>

      <div className="flex flex-1 overflow-y-auto">
        <div className="flex w-full max-w-3xl flex-col gap-4 p-6">
          {loading && !agentConfig ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <>
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="e.g. You are a Python data analysis expert. Always provide runnable code examples."
                className="min-h-[260px] resize-y font-mono text-sm leading-relaxed"
              />
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  {draft.length} / 8000 characters
                </span>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={handleReset} disabled={saving}>
                    Reset
                  </Button>
                  <Button size="sm" onClick={() => void handleSave()} disabled={saving}>
                    {saving ? 'Saving…' : 'Save'}
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
