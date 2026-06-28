'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { Avatar as DicebearAvatar, Style } from '@dicebear/core'
import glyphsDef from '@dicebear/styles/glyphs.json'
import notionistsDef from '@dicebear/styles/notionists.json'
import micahDef from '@dicebear/styles/micah.json'
import openPeepsDef from '@dicebear/styles/open-peeps.json'
import { Sparkles, RotateCcw, Upload, Pencil, Check } from 'lucide-react'
import { createApiClient, uploadAvatar, deleteAvatar, useAuthStore } from '@cubebox/core'
import { Avatar, type AvatarStyle } from '@/components/ui/avatar-resolved'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Button } from '@/components/ui/button'
import { randomSeed, svgToPngBlob } from '@/lib/avatar'

const PER_STYLE = 8

const STYLE_DEFS: { style: AvatarStyle; instance: Style<unknown> }[] = [
  { style: 'glyphs', instance: new Style(glyphsDef as never) },
  { style: 'notionists', instance: new Style(notionistsDef as never) },
  { style: 'micah', instance: new Style(micahDef as never) },
  { style: 'open-peeps', instance: new Style(openPeepsDef as never) },
]

type Pending =
  | { kind: 'generated'; seed: string; style: AvatarStyle }
  | { kind: 'uploaded'; blobUrl: string }
  | { kind: 'reset' }

function normalizeToPng(file: File): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const url = URL.createObjectURL(file)
    img.onload = () => {
      URL.revokeObjectURL(url)
      const canvas = document.createElement('canvas')
      canvas.width = 256
      canvas.height = 256
      const ctx = canvas.getContext('2d')!
      const scale = Math.min(256 / img.width, 256 / img.height)
      const w = img.width * scale
      const h = img.height * scale
      ctx.drawImage(img, (256 - w) / 2, (256 - h) / 2, w, h)
      canvas.toBlob((b) => {
        if (b) resolve(b)
        else reject(new Error('normalizeToPng: toBlob returned null'))
      }, 'image/png')
    }
    img.onerror = () => reject(new Error('normalizeToPng: failed to load image'))
    img.src = url
  })
}

function freshBatch(): Record<AvatarStyle, string[]> {
  const b = {} as Record<AvatarStyle, string[]>
  for (const def of STYLE_DEFS) {
    b[def.style] = Array.from({ length: PER_STYLE }, () => randomSeed())
  }
  return b
}

export function AvatarEditor() {
  const user = useAuthStore((s) => s.user)
  // Start empty so SSR and the first client render agree (randomSeed() is
  // non-deterministic — calling it in useState's initializer causes a
  // hydration mismatch). The real random batch is generated after mount.
  const [batch, setBatch] = useState<Record<AvatarStyle, string[]> | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const uploadedFile = useRef<File | null>(null)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- client-only random batch; generating in useState's initializer would hydrate-mismatch (randomSeed is non-deterministic)
    setBatch(freshBatch())
  }, [])

  // Discard any un-saved preview (and revoke object URL) when the popover closes.
  useEffect(() => {
    if (open) return
    if (pending?.kind === 'uploaded') URL.revokeObjectURL(pending.blobUrl)
    // eslint-disable-next-line react-hooks/set-state-in-effect -- discard un-saved preview on close (not a cascading render)
    setPending(null)
    uploadedFile.current = null
  }, [open, pending])

  const refreshUser = useCallback(async (client: ReturnType<typeof createApiClient>) => {
    await useAuthStore.getState().loadMe(client)
  }, [])

  function pickGenerated(seed: string, style: AvatarStyle) {
    if (pending?.kind === 'uploaded') URL.revokeObjectURL(pending.blobUrl)
    uploadedFile.current = null
    setPending({ kind: 'generated', seed, style })
  }

  function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    if (pending?.kind === 'uploaded') URL.revokeObjectURL(pending.blobUrl)
    uploadedFile.current = file
    setPending({ kind: 'uploaded', blobUrl: URL.createObjectURL(file) })
    e.target.value = ''
  }

  function onReset() {
    if (pending?.kind === 'uploaded') URL.revokeObjectURL(pending.blobUrl)
    uploadedFile.current = null
    setPending({ kind: 'reset' })
  }

  async function onSave() {
    if (!pending) return
    setBusy(true)
    try {
      const client = createApiClient('')
      if (pending.kind === 'generated') {
        const def = STYLE_DEFS.find((d) => d.style === pending.style)!
        const svg = new DicebearAvatar(def.instance, { seed: pending.seed, size: 256 }).toString()
        const png = await svgToPngBlob(svg, 256)
        await uploadAvatar(client, {
          file: new File([png], 'avatar.png', { type: 'image/png' }),
          kind: 'generated',
          seed: pending.seed,
          style: pending.style,
        })
      } else if (pending.kind === 'uploaded' && uploadedFile.current) {
        const png = await normalizeToPng(uploadedFile.current)
        await uploadAvatar(client, {
          file: new File([png], 'avatar.png', { type: 'image/png' }),
          kind: 'uploaded',
        })
      } else if (pending.kind === 'reset') {
        await deleteAvatar(client)
      }
      if (pending.kind === 'uploaded') URL.revokeObjectURL(pending.blobUrl)
      setPending(null)
      uploadedFile.current = null
      await refreshUser(client)
      setOpen(false)
    } finally {
      setBusy(false)
    }
  }

  // What the left preview renders: the pending selection if any, else the saved avatar.
  // For a pending generated pick or reset we must clear src so <Avatar>
  // renders the live DiceBear (otherwise the saved avatar URL wins). Reset
  // shows the default glyphs avatar seeded by the user id.
  const previewSrc =
    pending?.kind === 'uploaded'
      ? pending.blobUrl
      : pending?.kind === 'generated' || pending?.kind === 'reset'
        ? null
        : (user?.avatar_url ?? null)
  const previewSeed =
    pending?.kind === 'generated'
      ? pending.seed
      : pending?.kind === 'reset'
        ? (user?.id ?? null)
        : (user?.avatar_seed ?? user?.id ?? null)
  const previewStyle: AvatarStyle =
    pending?.kind === 'generated'
      ? pending.style
      : pending?.kind === 'reset'
        ? 'glyphs'
        : ((user?.avatar_style as AvatarStyle) ?? 'glyphs')

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            className="group relative inline-flex rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label="Change profile picture"
          >
            <Avatar
              src={user?.avatar_url ?? null}
              seed={user?.avatar_seed ?? user?.id ?? null}
              name={user?.display_name ?? null}
              style={(user?.avatar_style as AvatarStyle) ?? 'glyphs'}
              size="xl"
              loading={user === null}
            />
            <span className="pointer-events-none absolute -right-1 -bottom-1 flex size-6 items-center justify-center rounded-full bg-primary text-primary-foreground opacity-0 shadow-sm ring-2 ring-background transition-opacity group-hover:opacity-100">
              <Pencil className="size-3" />
            </span>
          </button>
        }
      />
      <PopoverContent align="start" sideOffset={8} className="w-[28rem] p-3">
        <div className="flex gap-3">
          {/* Left: preview, hover to upload */}
          <div className="flex flex-none flex-col items-center gap-2 pt-1">
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              disabled={busy}
              className="group/preview relative inline-flex rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              aria-label="Upload a photo"
            >
              <Avatar
                src={previewSrc}
                seed={previewSeed}
                name={user?.display_name ?? null}
                style={previewStyle}
                size="xl"
              />
              <span className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-1 rounded-full bg-black/55 text-xs font-medium text-white opacity-0 transition-opacity group-hover/preview:opacity-100">
                <Upload className="size-5" />
                Upload
              </span>
            </button>
            <input
              ref={fileInput}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={onUpload}
              disabled={busy}
            />
          </div>

          {/* Right: shuffle icon, reset icon, per-style rows, save */}
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <div className="flex justify-end gap-1">
              <button
                type="button"
                onClick={() => setBatch(freshBatch())}
                disabled={busy}
                className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
                aria-label="Shuffle generated avatars"
                title="Shuffle"
              >
                <Sparkles className="size-4" />
              </button>
              <button
                type="button"
                onClick={onReset}
                disabled={busy}
                className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
                aria-label="Reset to default avatar"
                title="Reset"
              >
                <RotateCcw className="size-4" />
              </button>
            </div>
            <div className="flex max-h-72 flex-col gap-1.5 overflow-y-auto p-0.5">
              {batch &&
                STYLE_DEFS.map((def) => (
                  <div key={def.style} className="flex gap-1.5">
                    {batch[def.style].map((seed) => (
                      <button
                        key={seed}
                        type="button"
                        onClick={() => pickGenerated(seed, def.style)}
                        disabled={busy}
                        className="flex size-9 items-center justify-center rounded-full border border-border hover:border-primary disabled:opacity-50"
                      >
                        <Avatar seed={seed} style={def.style} />
                      </button>
                    ))}
                  </div>
                ))}
            </div>
            <Button
              size="sm"
              onClick={onSave}
              disabled={!pending || busy}
              className="mt-0.5 self-end"
            >
              <Check className="size-4" />
              Save
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
