// frontend/packages/web/components/sandbox-env/EnvModal.tsx
'use client'

import { useState } from 'react'
import { X } from 'lucide-react'
import { type CreateEnvIn, type EnvEntryOut } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export type ModalMode =
  | { kind: 'add-org' }
  | { kind: 'add-workspace'; defaultScope: 'workspace' | 'user' }
  | { kind: 'rotate'; entry: EnvEntryOut }

interface Props {
  mode: ModalMode
  onSubmit: (
    body: CreateEnvIn | { secret_value: string },
    entryId?: string,
    scope?: 'workspace' | 'user',
  ) => Promise<void>
  onClose: () => void
}

const NAME_RE = /^[A-Z_][A-Z0-9_]*$/

function parseHosts(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

export function EnvModal({ mode, onSubmit, onClose }: Props) {
  const isRotate = mode.kind === 'rotate'

  const [name, setName] = useState(isRotate ? mode.entry.env_name : '')
  const [scope, setScope] = useState<'workspace' | 'user'>(
    mode.kind === 'add-workspace' ? mode.defaultScope : 'workspace',
  )
  const [isSecret, setIsSecret] = useState(true)
  const [value, setValue] = useState('')
  const [hostsRaw, setHostsRaw] = useState('')
  const [nameError, setNameError] = useState<string | null>(null)
  const [hostsError, setHostsError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  function validateName(v: string): string | null {
    if (!v) return 'Name is required'
    if (v.length > 128) return 'Max 128 characters'
    if (!NAME_RE.test(v))
      return 'Use letters, digits, or underscores; must start with a letter or underscore'
    return null
  }

  function validateHosts(raw: string): string | null {
    if (!isSecret) return null
    const hosts = parseHosts(raw)
    if (hosts.length === 0) return 'At least one host is required for secrets'
    const invalid = hosts.filter(
      (h) => !/^(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(h),
    )
    if (invalid.length > 0) return `Invalid host pattern: ${invalid[0]}`
    return null
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitError(null)

    const nErr = isRotate ? null : validateName(name)
    const hErr = isSecret ? validateHosts(hostsRaw) : null
    setNameError(nErr)
    setHostsError(hErr)
    if (nErr || hErr) return
    if (!value) {
      setSubmitError('Value is required')
      return
    }

    setSaving(true)
    try {
      if (isRotate) {
        await onSubmit({ secret_value: value }, mode.entry.id)
      } else {
        const body: CreateEnvIn = {
          env_name: name,
          is_secret: isSecret,
          ...(isSecret
            ? { secret_value: value, hosts: parseHosts(hostsRaw) }
            : { secret_value: value }),
        }
        // Pass the final scope selection (only relevant for workspace-mode adds)
        const finalScope = mode.kind === 'add-workspace' ? scope : undefined
        await onSubmit(body, undefined, finalScope)
      }
      onClose()
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : 'An error occurred')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="relative w-full max-w-md rounded-xl border border-border/70 bg-background p-6 shadow-xl">
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-muted-foreground hover:text-foreground"
        >
          <X className="size-4" />
        </button>

        <h2 className="mb-5 text-base font-semibold">
          {isRotate ? 'Rotate value' : 'Add environment variable'}
        </h2>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {/* NAME */}
          {!isRotate && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="env-name" className="text-xs font-medium">
                Name
              </Label>
              <Input
                id="env-name"
                value={name}
                onChange={(e) => setName(e.target.value.toUpperCase())}
                onBlur={() => setNameError(validateName(name))}
                className="font-mono text-sm"
                placeholder="VARIABLE_NAME"
                maxLength={128}
              />
              {nameError && <p className="text-xs text-destructive">{nameError}</p>}
            </div>
          )}

          {/* SCOPE — only for workspace-admin add */}
          {mode.kind === 'add-workspace' && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs font-medium">Scope</Label>
              <div className="flex gap-3">
                {(['workspace', 'user'] as const).map((s) => (
                  <label key={s} className="flex cursor-pointer items-center gap-1.5 text-sm">
                    <input
                      type="radio"
                      name="scope"
                      value={s}
                      checked={scope === s}
                      onChange={() => setScope(s)}
                    />
                    {s === 'workspace' ? 'Workspace' : 'Personal'}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* TYPE — only for add */}
          {!isRotate && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs font-medium">Type</Label>
              <div className="flex flex-col gap-2.5">
                <label className="flex cursor-pointer items-start gap-2.5">
                  <input
                    type="radio"
                    name="type"
                    className="mt-0.5"
                    checked={isSecret}
                    onChange={() => {
                      setIsSecret(true)
                      setValue('')
                    }}
                  />
                  <div>
                    <div className="text-sm font-medium">Secret token</div>
                    <div className="text-xs text-muted-foreground">
                      Injected as a placeholder. The egress proxy substitutes the real value into
                      outbound HTTP request headers at runtime. Requires allowed hosts.
                    </div>
                  </div>
                </label>
                <label className="flex cursor-pointer items-start gap-2.5">
                  <input
                    type="radio"
                    name="type"
                    className="mt-0.5"
                    checked={!isSecret}
                    onChange={() => {
                      setIsSecret(false)
                      setValue('')
                    }}
                  />
                  <div>
                    <div className="text-sm font-medium">Env value</div>
                    <div className="text-xs text-muted-foreground">
                      The actual value is injected directly into the sandbox env var. Encrypted at
                      rest.
                    </div>
                  </div>
                </label>
              </div>
            </div>
          )}

          {/* VALUE */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="env-value" className="text-xs font-medium">
              {isRotate ? 'New secret value' : 'Value'}
            </Label>
            <Input
              id="env-value"
              type="password"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className="font-mono text-sm"
              placeholder="••••••••"
              autoComplete="off"
              maxLength={isSecret ? undefined : 4096}
            />
          </div>

          {/* HOSTS — only for secrets */}
          {isSecret && !isRotate && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="env-hosts" className="text-xs font-medium">
                Allowed hosts{' '}
                <span className="font-normal text-muted-foreground">
                  (space or comma separated)
                </span>
              </Label>
              <Input
                id="env-hosts"
                value={hostsRaw}
                onChange={(e) => setHostsRaw(e.target.value)}
                onBlur={() => setHostsError(validateHosts(hostsRaw))}
                placeholder="api.github.com *.example.com"
                className="text-sm"
              />
              {hostsError && <p className="text-xs text-destructive">{hostsError}</p>}
            </div>
          )}

          {/* Submit error */}
          {submitError && (
            <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {submitError}
            </p>
          )}

          {/* Footer */}
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={saving}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={saving}>
              {saving ? 'Saving…' : isRotate ? 'Rotate' : 'Add'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
