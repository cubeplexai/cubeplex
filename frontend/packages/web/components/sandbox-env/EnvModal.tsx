'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { X } from 'lucide-react'
import { type CreateEnvIn, type EnvEntryOut, type UpdateEntryIn } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export type ModalMode =
  | { kind: 'add-org' }
  | { kind: 'add-workspace'; defaultScope: 'workspace' | 'user' }
  | { kind: 'edit'; entry: EnvEntryOut }

interface Props {
  mode: ModalMode
  onSubmit: (
    body: CreateEnvIn | UpdateEntryIn,
    entryId?: string,
    scope?: 'workspace' | 'user',
  ) => Promise<void>
  onClose: () => void
}

function parseHosts(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function hostsToRaw(hosts: string[] | null | undefined): string {
  return hosts ? hosts.join(' ') : ''
}

export function EnvModal({ mode, onSubmit, onClose }: Props) {
  const t = useTranslations('wsSettings.sandboxEnv')
  const isEdit = mode.kind === 'edit'
  const entry = isEdit ? mode.entry : null

  const [name, setName] = useState(entry?.env_name ?? '')
  const [scope, setScope] = useState<'workspace' | 'user'>(
    mode.kind === 'add-workspace' ? mode.defaultScope : 'workspace',
  )
  const [isSecret, setIsSecret] = useState(entry ? entry.is_secret : true)
  const [value, setValue] = useState('')
  const [hostsRaw, setHostsRaw] = useState(hostsToRaw(entry?.hosts))
  const [headerNamesRaw, setHeaderNamesRaw] = useState(
    entry?.header_names ? entry.header_names.join(' ') : '',
  )
  const [nameError, setNameError] = useState<string | null>(null)
  const [hostsError, setHostsError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const NAME_RE = /^[A-Z_][A-Z0-9_]*$/

  function validateName(v: string): string | null {
    if (!v) return t('validNameRequired')
    if (v.length > 128) return t('validNameMax')
    if (!NAME_RE.test(v)) return t('validNameFormat')
    return null
  }

  function isValidHostPattern(h: string): boolean {
    // Backend also accepts anchored regex patterns like /^api\.example\.com$/
    if (/^\/\^.*\$\/$/.test(h)) return true
    return /^(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(h)
  }

  function validateHosts(raw: string): string | null {
    if (!isSecret) return null
    const hosts = parseHosts(raw)
    if (hosts.length === 0) return t('validHostRequired')
    const invalid = hosts.filter((h) => !isValidHostPattern(h))
    if (invalid.length > 0) return t('validHostInvalid', { host: invalid[0] })
    return null
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitError(null)

    if (isEdit) {
      const hErr = isSecret ? validateHosts(hostsRaw) : null
      setHostsError(hErr)
      if (hErr) return

      const body: UpdateEntryIn = {}
      if (isSecret) {
        body.hosts = parseHosts(hostsRaw)
        body.header_names = headerNamesRaw.trim()
          ? headerNamesRaw
              .split(/[\s,]+/)
              .map((s) => s.trim())
              .filter(Boolean)
          : null
      }
      if (value) body.secret_value = value

      if (!body.hosts && !body.secret_value) {
        setSubmitError(t('errorNoChanges'))
        return
      }

      setSaving(true)
      try {
        await onSubmit(body, entry!.id)
        onClose()
      } catch (err: unknown) {
        setSubmitError(err instanceof Error ? err.message : t('errorFallback'))
      } finally {
        setSaving(false)
      }
      return
    }

    // Add mode
    const nErr = validateName(name)
    const hErr = isSecret ? validateHosts(hostsRaw) : null
    setNameError(nErr)
    setHostsError(hErr)
    if (nErr || hErr) return
    if (!value) {
      setSubmitError(t('validNameRequired'))
      return
    }

    setSaving(true)
    try {
      const body: CreateEnvIn = {
        env_name: name,
        is_secret: isSecret,
        ...(isSecret
          ? { secret_value: value, hosts: parseHosts(hostsRaw) }
          : { secret_value: value }),
      }
      const finalScope = mode.kind === 'add-workspace' ? scope : undefined
      await onSubmit(body, undefined, finalScope)
      onClose()
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : t('errorFallback'))
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
          {isEdit ? t('modalEditTitle') : t('modalAddTitle')}
        </h2>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {/* NAME */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="env-name" className="text-xs font-medium">
              {t('fieldName')}
            </Label>
            {isEdit ? (
              <div className="rounded-md border border-border bg-muted/30 px-3 py-2 font-mono text-sm text-muted-foreground">
                {entry!.env_name}
              </div>
            ) : (
              <>
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
              </>
            )}
          </div>

          {/* SCOPE */}
          {mode.kind === 'add-workspace' && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs font-medium">{t('fieldScope')}</Label>
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
                    {s === 'workspace' ? t('scopeWorkspace') : t('scopePersonal')}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* TYPE */}
          {isEdit ? (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs font-medium">{t('fieldType')}</Label>
              <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
                {entry!.is_secret ? t('typeSecretLabel') : t('typeEnvLabel')}
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs font-medium">{t('fieldType')}</Label>
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
                    <div className="text-sm font-medium">{t('typeSecretLabel')}</div>
                    <div className="text-xs text-muted-foreground">{t('typeSecretDesc')}</div>
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
                    <div className="text-sm font-medium">{t('typeEnvLabel')}</div>
                    <div className="text-xs text-muted-foreground">{t('typeEnvDesc')}</div>
                  </div>
                </label>
              </div>
            </div>
          )}

          {/* HOSTS */}
          {isSecret && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="env-hosts" className="text-xs font-medium">
                {t('fieldHosts')}{' '}
                <span className="font-normal text-muted-foreground">{t('fieldHostsHint')}</span>
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

          {/* HEADER NAMES */}
          {isSecret && isEdit && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="env-header-names" className="text-xs font-medium">
                {t('fieldHeaderNames')}{' '}
                <span className="font-normal text-muted-foreground">
                  {t('fieldHeaderNamesHint')}
                </span>
              </Label>
              <Input
                id="env-header-names"
                value={headerNamesRaw}
                onChange={(e) => setHeaderNamesRaw(e.target.value)}
                placeholder="authorization x-api-key"
                className="text-sm"
              />
            </div>
          )}

          {/* VALUE */}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="env-value" className="text-xs font-medium">
              {isEdit ? t('fieldValueEdit') : t('fieldValue')}
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

          {submitError && (
            <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {submitError}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={onClose} disabled={saving}>
              {t('btnCancel')}
            </Button>
            <Button type="submit" size="sm" disabled={saving}>
              {saving ? t('btnSaving') : isEdit ? t('btnSave') : t('btnAdd')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
