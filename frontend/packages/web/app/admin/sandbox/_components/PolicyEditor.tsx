'use client'

import { useEffect, useMemo, useState } from 'react'
import { Check, Info } from 'lucide-react'
import {
  createApiClient,
  getSandboxPolicy,
  putSandboxPolicy,
  type SandboxCommandRule,
  type SandboxNetworkRule,
  type SandboxPolicyOut,
} from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import { NetworkRulesTable } from './NetworkRulesTable'
import { CommandRulesTable } from './CommandRulesTable'
import { CredentialConflictBanner } from './CredentialConflictBanner'

interface Draft {
  defaultImage: string
  networkDefaultAction: 'allow' | 'deny'
  networkRules: SandboxNetworkRule[]
  commandRules: SandboxCommandRule[]
  egressProxy: string
}

function fromServer(p: SandboxPolicyOut): Draft {
  return {
    defaultImage: p.default_image,
    networkDefaultAction: p.network_default_action,
    networkRules: p.network_rules ?? [],
    commandRules: p.command_rules ?? [],
    egressProxy: p.egress_proxy ?? '',
  }
}

function rulesEqual<T extends object>(a: T[], b: T[]): boolean {
  if (a.length !== b.length) return false
  return a.every((row, i) => JSON.stringify(row) === JSON.stringify(b[i]))
}

function isDirty(a: Draft, b: Draft): boolean {
  if (a.defaultImage !== b.defaultImage) return true
  if (a.networkDefaultAction !== b.networkDefaultAction) return true
  if (!rulesEqual(a.networkRules, b.networkRules)) return true
  if (!rulesEqual(a.commandRules, b.commandRules)) return true
  if (a.egressProxy !== b.egressProxy) return true
  return false
}

export function PolicyEditor() {
  const client = useMemo(() => createApiClient(''), [])

  const [server, setServer] = useState<Draft | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])

  useEffect(() => {
    let cancelled = false
    getSandboxPolicy(client)
      .then((p) => {
        if (cancelled) return
        const next = fromServer(p)
        setServer(next)
        setDraft(next)
      })
      .catch((e: Error) => {
        if (!cancelled) setLoadError(e.message)
      })
    return () => {
      cancelled = true
    }
  }, [client])

  useEffect(() => {
    if (!savedAt) return
    const id = setTimeout(() => setSavedAt(null), 2500)
    return () => clearTimeout(id)
  }, [savedAt])

  if (loadError) {
    return (
      <section className="rounded-xl border border-destructive/50 bg-destructive/5 p-5 text-sm text-destructive">
        Failed to load sandbox policy: {loadError}
      </section>
    )
  }
  if (!server || !draft) {
    return (
      <section className="rounded-xl border border-border/70 bg-card/40 p-5 text-xs text-muted-foreground shadow-sm">
        Loading…
      </section>
    )
  }

  const dirty = isDirty(server, draft)

  const setDefaultImage = (v: string) => {
    setDraft({ ...draft, defaultImage: v })
    setSavedAt(null)
    setSaveError(null)
  }
  const setNetworkDefaultAction = (v: 'allow' | 'deny') => {
    setDraft({ ...draft, networkDefaultAction: v })
    setSavedAt(null)
    setSaveError(null)
  }
  const setNetworkRules = (next: SandboxNetworkRule[]) => {
    setDraft({ ...draft, networkRules: next })
    setSavedAt(null)
    setSaveError(null)
  }
  const setCommandRules = (next: SandboxCommandRule[]) => {
    setDraft({ ...draft, commandRules: next })
    setSavedAt(null)
    setSaveError(null)
  }
  const setEgressProxy = (v: string) => {
    setDraft({ ...draft, egressProxy: v })
    setSavedAt(null)
    setSaveError(null)
  }

  const discard = () => {
    setDraft(server)
    setSaveError(null)
    setWarnings([])
  }

  const save = async () => {
    if (!dirty || saving) return
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await putSandboxPolicy(client, {
        default_image: draft.defaultImage,
        network_default_action: draft.networkDefaultAction,
        network_rules: draft.networkRules,
        command_rules: draft.commandRules,
        egress_proxy: draft.egressProxy.trim() || null,
      })
      const next = fromServer(updated)
      setServer(next)
      setDraft(next)
      setWarnings(updated.warnings ?? [])
      setSavedAt(Date.now())
    } catch (e) {
      setSaveError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {warnings.length > 0 && <CredentialConflictBanner warnings={warnings} />}

      <SectionCard>
        <SectionHeader
          title="Default sandbox image"
          subtitle="The container image used when a workspace member starts a new sandbox."
        />
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="sandbox-default-image">Image</Label>
          <Input
            id="sandbox-default-image"
            data-testid="sandbox-policy-default-image"
            value={draft.defaultImage}
            onChange={(e) => setDefaultImage(e.target.value)}
            placeholder="ubuntu:22.04"
            className="font-mono text-xs"
          />
          <p className="mt-1 flex items-start gap-1.5 text-[11px] text-muted-foreground">
            <Info className="mt-0.5 size-3 shrink-0" />
            <span>
              Changes apply lazily — existing sandboxes finish on their original image; new
              conversations pick up the new image.
            </span>
          </p>
        </div>
      </SectionCard>

      <SectionCard>
        <SectionHeader
          title="Network rules"
          subtitle="Restrict which hosts the sandbox can reach over the network. Order matters — earlier rules win."
        />
        <NetworkRulesTable
          rules={draft.networkRules}
          defaultAction={draft.networkDefaultAction}
          onChangeDefaultAction={setNetworkDefaultAction}
          onChange={setNetworkRules}
          disabled={saving}
        />
      </SectionCard>

      <SectionCard>
        <SectionHeader
          title="Command rules"
          subtitle="Allow, deny, or flag specific shell commands by glob pattern."
        />
        <CommandRulesTable
          rules={draft.commandRules}
          onChange={setCommandRules}
          disabled={saving}
        />
      </SectionCard>

      <SectionCard>
        <SectionHeader
          title="Egress proxy"
          subtitle="Route all sandbox outbound traffic through an HTTP proxy for security inspection, traffic mirroring, or network policy enforcement. Leave empty to connect directly."
        />
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="sandbox-egress-proxy">Proxy URL</Label>
          <Input
            id="sandbox-egress-proxy"
            data-testid="sandbox-policy-egress-proxy"
            value={draft.egressProxy}
            onChange={(e) => setEgressProxy(e.target.value)}
            placeholder="http://proxy.internal:8080"
            className="font-mono text-xs"
          />
          <p className="mt-1 flex items-start gap-1.5 text-[11px] text-muted-foreground">
            <Info className="mt-0.5 size-3 shrink-0" />
            <span>
              Only http:// and https:// schemes are supported. Changes apply to new sandboxes only —
              existing sandboxes keep their current config until restarted.
            </span>
          </p>
        </div>
      </SectionCard>

      <div className="sticky bottom-0 -mx-1 flex items-center justify-end gap-2 rounded-lg border border-border/60 bg-background/95 px-3 py-2.5 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-background/80">
        {saveError && (
          <span
            className="mr-auto text-xs text-destructive"
            data-testid="sandbox-policy-save-error"
          >
            {saveError}
          </span>
        )}
        {!saveError && savedAt && (
          <span
            className={cn(
              'mr-auto inline-flex items-center gap-1 text-xs',
              'text-emerald-600 dark:text-emerald-400',
            )}
            data-testid="sandbox-policy-saved"
          >
            <Check className="size-3" />
            Saved
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={discard}
          disabled={!dirty || saving}
          data-testid="sandbox-policy-discard"
        >
          Discard
        </Button>
        <Button
          size="sm"
          onClick={() => void save()}
          disabled={!dirty || saving}
          data-testid="sandbox-policy-save"
        >
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-4 rounded-xl border border-border/70 bg-card/40 p-5 shadow-sm">
      {children}
    </section>
  )
}

function SectionHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <header className="flex flex-col gap-0.5 border-b border-border/60 pb-3">
      <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
      {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
    </header>
  )
}
