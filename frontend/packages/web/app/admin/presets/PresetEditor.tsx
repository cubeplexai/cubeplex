'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { ArrowDown, ArrowUp, GripVertical, Loader2, Plus, Trash2 } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { jsonHeaders } from '@/lib/csrf'
import type { AdminModelPresetsBody, AdminPresetEntry, TaskPresetKey } from '@/lib/types/presets'
import type { AdminModelPresetsResponse } from '@/lib/api/presets'
import { AdminPageShell } from '@/components/management/AdminPageShell'
import { cn } from '@/lib/utils'

interface PresetEditorProps {
  initial: AdminModelPresetsResponse
  availableModels: string[]
}

const TASK_KEYS: TaskPresetKey[] = ['title', 'compaction', 'summarize']
const NOT_SET = '__not_set__'

// Static map so next-intl's typed-key check + the i18n-key parity script can
// see exactly which message keys are referenced (no dynamic `t(TASK_LABEL_KEY[key])`).
const TASK_LABEL_KEY = {
  title: 'task_title',
  compaction: 'task_compaction',
  summarize: 'task_summarize',
} as const

interface ApiError {
  status: string
  error_code?: string
  message?: string
  details?: string
  data?: unknown
}

export function extractMissingRefs(data: unknown): string[] {
  if (typeof data !== 'object' || data === null) return []
  const refs = (data as { missing_refs?: unknown }).missing_refs
  if (!Array.isArray(refs)) return []
  return refs.filter((r): r is string => typeof r === 'string')
}

// Fallback: backend BrokenPresetError pre-data-field serialized missing_refs
// as `details="missing_refs=['a/b', 'c/d']"`. Parse it back into a string[].
// Returns [] when the shape doesn't match. Kept for back-compat with older
// servers whose responses do not include the structured `data` field.
export function parseMissingRefs(details: string | undefined): string[] {
  if (!details) return []
  const match = details.match(/missing_refs=\[(.*)\]/)
  if (!match) return []
  const inner = match[1].trim()
  if (!inner) return []
  return inner
    .split(',')
    .map((s) => s.trim().replace(/^['"]|['"]$/g, ''))
    .filter(Boolean)
}

function emptyBody(): AdminModelPresetsBody {
  return { presets: [], task_presets: {} }
}

/**
 * Move an array entry from index `from` to index `to`. `to` is the index the
 * moved item should occupy in the final array. Used by both arrow buttons
 * (`reorder(arr, idx, idx ± 1)`) and HTML5 drag-drop
 * (`reorder(arr, draggingIdx, dropTargetIdx)`) — the splice-after-removal
 * shape gives "moved item lands at target's original slot" for both
 * directions without any off-by-one adjustment.
 *
 * Exported for unit tests.
 */
export function reorder<T>(arr: readonly T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= arr.length || to >= arr.length) {
    return [...arr]
  }
  const next = [...arr]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

export function PresetEditor({ initial, availableModels }: PresetEditorProps): React.ReactElement {
  const t = useTranslations('adminPresets')
  const [body, setBody] = useState<AdminModelPresetsBody>(() => initial.value ?? emptyBody())
  const [origin, setOrigin] = useState(initial.origin)
  const [saving, setSaving] = useState(false)
  const [banner, setBanner] = useState<string | null>(null)
  const [missingRefs, setMissingRefs] = useState<Set<string>>(new Set())
  const [draggingIdx, setDraggingIdx] = useState<number | null>(null)

  const labelSet = useMemo(() => body.presets.map((p) => p.label), [body.presets])
  const duplicateLabels = useMemo(() => {
    const seen = new Set<string>()
    const dups = new Set<string>()
    for (const label of labelSet) {
      const norm = label.trim()
      if (!norm) continue
      if (seen.has(norm)) dups.add(norm)
      else seen.add(norm)
    }
    return dups
  }, [labelSet])

  const updatePreset = (idx: number, patch: Partial<AdminPresetEntry>): void => {
    setBody((b) => ({
      ...b,
      presets: b.presets.map((p, i) => (i === idx ? { ...p, ...patch } : p)),
    }))
  }

  const setDefault = (idx: number): void => {
    setBody((b) => ({
      ...b,
      presets: b.presets.map((p, i) => ({ ...p, is_default: i === idx })),
    }))
  }

  const movePreset = (from: number, to: number): void => {
    if (to < 0 || to >= body.presets.length) return
    setBody((b) => ({ ...b, presets: reorder(b.presets, from, to) }))
  }

  const addPreset = (): void => {
    setBody((b) => ({
      ...b,
      presets: [
        ...b.presets,
        {
          label: '',
          chain: [],
          is_default: b.presets.length === 0,
        },
      ],
    }))
  }

  const removePreset = (idx: number): void => {
    setBody((b) => {
      const next = b.presets.filter((_, i) => i !== idx)
      // Drop task_presets values that pointed at the removed label.
      const removedLabel = b.presets[idx]?.label
      const taskPresets = { ...b.task_presets }
      if (removedLabel) {
        for (const k of TASK_KEYS) {
          if (taskPresets[k] === removedLabel) delete taskPresets[k]
        }
      }
      // Ensure exactly one default if any presets remain.
      const hasDefault = next.some((p) => p.is_default)
      if (!hasDefault && next.length > 0) next[0] = { ...next[0], is_default: true }
      return { ...b, presets: next, task_presets: taskPresets }
    })
  }

  const moveChainEntry = (presetIdx: number, from: number, to: number): void => {
    const preset = body.presets[presetIdx]
    if (!preset || to < 0 || to >= preset.chain.length) return
    updatePreset(presetIdx, { chain: reorder(preset.chain, from, to) })
  }

  const removeChainEntry = (presetIdx: number, chainIdx: number): void => {
    const preset = body.presets[presetIdx]
    if (!preset) return
    updatePreset(presetIdx, { chain: preset.chain.filter((_, i) => i !== chainIdx) })
  }

  const addChainEntry = (presetIdx: number, ref: string): void => {
    const preset = body.presets[presetIdx]
    if (!preset || !ref) return
    updatePreset(presetIdx, { chain: [...preset.chain, ref] })
  }

  const setTaskPreset = (key: TaskPresetKey, value: string): void => {
    setBody((b) => {
      const next: Partial<Record<TaskPresetKey, string>> = { ...b.task_presets }
      if (value === NOT_SET) delete next[key]
      else next[key] = value
      return { ...b, task_presets: next }
    })
  }

  const buildPutBody = (): AdminModelPresetsBody => {
    // Omit any unset task_presets keys (Partial type — backend rejects empty strings).
    const taskPresets: Partial<Record<TaskPresetKey, string>> = {}
    for (const k of TASK_KEYS) {
      const v = body.task_presets[k]
      if (v) taskPresets[k] = v
    }
    return {
      presets: body.presets.map((p) => ({
        label: p.label.trim(),
        chain: p.chain,
        is_default: p.is_default,
      })),
      task_presets: taskPresets,
    }
  }

  const handleSave = async (): Promise<void> => {
    setSaving(true)
    setBanner(null)
    setMissingRefs(new Set())

    const put = buildPutBody()
    try {
      const res = await fetch('/api/v1/admin/model-presets', {
        method: 'PUT',
        credentials: 'include',
        headers: jsonHeaders(),
        body: JSON.stringify(put),
      })
      if (!res.ok) {
        const data: ApiError = await res.json().catch(() => ({ status: 'error' }))
        if (data.error_code === 'broken_preset') {
          // Prefer the structured `data.missing_refs` payload; fall back to
          // parsing the Python-repr `details` string for older servers.
          let refs = extractMissingRefs(data.data)
          if (refs.length === 0) {
            refs = parseMissingRefs(data.details)
          }
          setMissingRefs(new Set(refs))
          setBanner(t('errorBrokenPreset'))
        } else {
          setBanner(data.message ?? t('errorGeneric', { status: res.status }))
        }
        return
      }
      const data = (await res.json()) as AdminModelPresetsResponse
      setBody(data.value ?? emptyBody())
      setOrigin(data.origin)
    } catch (err) {
      setBanner((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <AdminPageShell
      title={t('title')}
      description={
        <>
          {t('subtitle')}
          {origin !== 'org' ? (
            <span className="ml-2 rounded-full bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
              {origin === 'system' ? t('originSystem') : t('originNone')}
            </span>
          ) : null}
        </>
      }
      action={
        <Button onClick={() => void handleSave()} disabled={saving} aria-label={t('save')}>
          {saving ? <Loader2 className="size-4 animate-spin" /> : null}
          <span>{t('save')}</span>
        </Button>
      }
    >
      {banner && (
        <Alert variant="destructive" role="alert">
          <AlertTitle>{t('errorTitle')}</AlertTitle>
          <AlertDescription>{banner}</AlertDescription>
        </Alert>
      )}

      <section aria-label={t('presetsSectionAria')} className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">{t('presetsHeading')}</h3>
          <Button variant="outline" size="sm" onClick={addPreset}>
            <Plus className="size-3.5" />
            <span>{t('addPreset')}</span>
          </Button>
        </div>

        {body.presets.length === 0 && (
          <p className="rounded-md border border-dashed border-border/70 px-4 py-6 text-center text-sm text-muted-foreground">
            {t('emptyPresets')}
          </p>
        )}

        <RadioGroup
          value={body.presets.findIndex((p) => p.is_default).toString()}
          onValueChange={(v) => {
            const n = Number(v)
            if (Number.isFinite(n)) setDefault(n)
          }}
          className="space-y-3"
        >
          {body.presets.map((preset, idx) => {
            const isDuplicate = duplicateLabels.has(preset.label.trim())
            const isEmptyLabel = preset.label.trim().length === 0
            return (
              <div
                key={idx}
                draggable
                onDragStart={() => setDraggingIdx(idx)}
                onDragOver={(e) => {
                  e.preventDefault()
                }}
                onDrop={(e) => {
                  e.preventDefault()
                  if (draggingIdx !== null && draggingIdx !== idx) {
                    movePreset(draggingIdx, idx)
                  }
                  setDraggingIdx(null)
                }}
                onDragEnd={() => setDraggingIdx(null)}
                className={cn(
                  'rounded-lg border border-border/70 bg-card/40 p-4',
                  draggingIdx === idx && 'opacity-50',
                )}
                data-testid={`preset-row-${idx}`}
              >
                <div className="flex items-start gap-3">
                  <button
                    type="button"
                    aria-label={t('dragHandle')}
                    className="mt-1 cursor-grab text-muted-foreground hover:text-foreground"
                  >
                    <GripVertical className="size-4" />
                  </button>

                  <div className="flex-1 space-y-3">
                    <div className="flex items-center gap-3">
                      <div className="flex-1">
                        <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
                          {t('label')}
                        </Label>
                        <Input
                          value={preset.label}
                          onChange={(e) => updatePreset(idx, { label: e.target.value })}
                          placeholder={t('labelPlaceholder')}
                          aria-invalid={isEmptyLabel || isDuplicate || undefined}
                          className={cn((isEmptyLabel || isDuplicate) && 'border-destructive')}
                        />
                        {isDuplicate && (
                          <p className="mt-1 text-xs text-destructive">
                            {t('errorDuplicateLabel')}
                          </p>
                        )}
                      </div>

                      <div className="flex items-center gap-2 self-end pb-1.5">
                        <RadioGroupItem value={idx.toString()} id={`default-${idx}`} />
                        <Label htmlFor={`default-${idx}`} className="text-xs">
                          {t('isDefault')}
                        </Label>
                      </div>

                      <div className="flex items-center gap-1 self-end pb-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('moveUp')}
                          onClick={() => movePreset(idx, idx - 1)}
                          disabled={idx === 0}
                        >
                          <ArrowUp className="size-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('moveDown')}
                          onClick={() => movePreset(idx, idx + 1)}
                          disabled={idx === body.presets.length - 1}
                        >
                          <ArrowDown className="size-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('removePreset')}
                          onClick={() => removePreset(idx)}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    </div>

                    <ChainEditor
                      chain={preset.chain}
                      availableModels={availableModels}
                      missingRefs={missingRefs}
                      onMove={(from, to) => moveChainEntry(idx, from, to)}
                      onRemove={(chainIdx) => removeChainEntry(idx, chainIdx)}
                      onAdd={(ref) => addChainEntry(idx, ref)}
                    />
                  </div>
                </div>
              </div>
            )
          })}
        </RadioGroup>
      </section>

      <section aria-label={t('taskPresetsAria')} className="space-y-3">
        <h3 className="text-sm font-medium">{t('taskPresetsHeading')}</h3>
        <p className="text-xs text-muted-foreground">{t('taskPresetsHint')}</p>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {TASK_KEYS.map((key) => (
            <div key={key} className="space-y-1">
              <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {t(TASK_LABEL_KEY[key])}
              </Label>
              <Select
                value={body.task_presets[key] ?? NOT_SET}
                onValueChange={(v) => setTaskPreset(key, v ?? NOT_SET)}
              >
                <SelectTrigger aria-label={t(TASK_LABEL_KEY[key])}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NOT_SET}>{t('notSet')}</SelectItem>
                  {labelSet
                    .map((label) => label.trim())
                    .filter((label, i, arr) => label && arr.indexOf(label) === i)
                    .map((label) => (
                      <SelectItem key={label} value={label}>
                        {label}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
          ))}
        </div>
      </section>
    </AdminPageShell>
  )
}

interface ChainEditorProps {
  chain: string[]
  availableModels: string[]
  missingRefs: Set<string>
  onMove: (from: number, to: number) => void
  onRemove: (idx: number) => void
  onAdd: (ref: string) => void
}

function ChainEditor({
  chain,
  availableModels,
  missingRefs,
  onMove,
  onRemove,
  onAdd,
}: ChainEditorProps): React.ReactElement {
  const t = useTranslations('adminPresets')
  const [draft, setDraft] = useState('')
  const [open, setOpen] = useState(false)

  const filtered = useMemo(() => {
    const q = draft.trim().toLowerCase()
    const pool = availableModels.filter((m) => !chain.includes(m))
    if (!q) return pool.slice(0, 20)
    return pool.filter((m) => m.toLowerCase().includes(q)).slice(0, 20)
  }, [draft, availableModels, chain])

  const handleAdd = (ref: string): void => {
    if (!ref.trim()) return
    onAdd(ref.trim())
    setDraft('')
    setOpen(false)
  }

  return (
    <div className="space-y-2">
      <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {t('chain')}
      </Label>
      {chain.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t('chainEmpty')}</p>
      ) : (
        <ol className="space-y-1">
          {chain.map((ref, i) => {
            const missing = missingRefs.has(ref)
            return (
              <li
                key={`${ref}-${i}`}
                className={cn(
                  'flex items-center gap-2 rounded-md border border-border/60 bg-background px-2.5 py-1.5 text-sm',
                  missing && 'border-destructive bg-destructive/5',
                )}
                title={missing ? t('missingRefTitle', { ref }) : undefined}
                data-testid={`chain-entry-${i}`}
              >
                <span className="text-xs text-muted-foreground">{i + 1}.</span>
                <span className={cn('flex-1 font-mono text-xs', missing && 'text-destructive')}>
                  {ref}
                </span>
                {missing && (
                  <span className="text-[10px] uppercase tracking-wide text-destructive">
                    {t('missingRefBadge')}
                  </span>
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('chainMoveUp')}
                  onClick={() => onMove(i, i - 1)}
                  disabled={i === 0}
                >
                  <ArrowUp className="size-3" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('chainMoveDown')}
                  onClick={() => onMove(i, i + 1)}
                  disabled={i === chain.length - 1}
                >
                  <ArrowDown className="size-3" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('chainRemove')}
                  onClick={() => onRemove(i)}
                >
                  <Trash2 className="size-3" />
                </Button>
              </li>
            )
          })}
        </ol>
      )}

      <div className="relative">
        <div className="flex items-center gap-2">
          <Input
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value)
              setOpen(true)
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => {
              // Defer closing so click on a list item fires first.
              window.setTimeout(() => setOpen(false), 120)
            }}
            placeholder={t('addModelPlaceholder')}
            aria-label={t('addModelAria')}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                handleAdd(draft)
              }
            }}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleAdd(draft)}
            disabled={!draft.trim()}
          >
            <Plus className="size-3.5" />
            <span>{t('chainAdd')}</span>
          </Button>
        </div>
        {open && filtered.length > 0 && (
          <ul
            role="listbox"
            className="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-md border border-border bg-popover py-1 text-sm shadow-md"
          >
            {filtered.map((ref) => (
              <li
                key={ref}
                role="option"
                aria-selected={false}
                className="cursor-pointer px-2.5 py-1 font-mono text-xs hover:bg-accent hover:text-accent-foreground"
                onMouseDown={(e) => {
                  e.preventDefault()
                  handleAdd(ref)
                }}
              >
                {ref}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
