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

  const handleSave = async () => {
    setSaving(true)
    try {
      await savePersona(client(), draft)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = () => {
    setDraft(agentConfig?.system_prompt ?? '')
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto p-8 max-w-2xl">
      <h2 className="text-base font-semibold mb-1">Persona</h2>
      <p className="text-sm text-muted-foreground mb-6">
        Define the agent&apos;s persona for every conversation in this workspace. Appended after the
        base system prompt.
      </p>

      {loading && !agentConfig ? (
        <div className="text-sm text-muted-foreground">Loading…</div>
      ) : (
        <>
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="e.g. You are a Python data analysis expert. Always provide runnable code examples."
            className="min-h-[200px] font-mono text-sm resize-y"
          />
          <div className="flex items-center justify-between mt-4">
            <span className="text-xs text-muted-foreground">{draft.length} characters</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={handleReset} disabled={saving}>
                Reset
              </Button>
              <Button size="sm" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
