'use client'

import { useState } from 'react'
import { createAvatar } from '@dicebear/core'
import { notionists } from '@dicebear/collection'
import { createApiClient, uploadAvatar, deleteAvatar, useAuthStore } from '@cubebox/core'
import { Avatar } from '@/components/ui/avatar-resolved'
import { randomSeed, svgToPngBlob } from '@/lib/avatar'

const GALLERY = 30

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

export function AvatarEditor() {
  const user = useAuthStore((s) => s.user)
  const [batch, setBatch] = useState<string[]>(() =>
    Array.from({ length: GALLERY }, () => randomSeed()),
  )
  const [busy, setBusy] = useState(false)

  async function refreshUser(client: ReturnType<typeof createApiClient>) {
    await useAuthStore.getState().loadMe(client)
  }

  async function applyGenerated(seed: string) {
    setBusy(true)
    try {
      const client = createApiClient('')
      const svg = createAvatar(notionists, { seed, size: 256 }).toString()
      const png = await svgToPngBlob(svg, 256)
      await uploadAvatar(client, {
        file: new File([png], 'avatar.png', { type: 'image/png' }),
        kind: 'generated',
        seed,
        style: 'notionists',
      })
      await refreshUser(client)
    } finally {
      setBusy(false)
    }
  }

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true)
    try {
      const client = createApiClient('')
      const png = await normalizeToPng(file)
      await uploadAvatar(client, {
        file: new File([png], 'avatar.png', { type: 'image/png' }),
        kind: 'uploaded',
      })
      await refreshUser(client)
    } finally {
      setBusy(false)
    }
  }

  async function onReset() {
    setBusy(true)
    try {
      const client = createApiClient('')
      await deleteAvatar(client)
      await refreshUser(client)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center gap-4">
        <Avatar
          src={user?.avatar_url ?? null}
          seed={user?.avatar_seed ?? user?.id ?? null}
          name={user?.display_name ?? null}
          size="lg"
        />
        <div className="flex gap-2">
          <label className="cursor-pointer rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90">
            Upload
            <input
              type="file"
              accept="image/*"
              className="hidden"
              onChange={onUpload}
              disabled={busy}
            />
          </label>
          <button
            className="rounded-md border bg-background px-4 py-2 text-sm font-medium hover:bg-accent"
            onClick={() => setBatch(Array.from({ length: GALLERY }, () => randomSeed()))}
            disabled={busy}
          >
            Shuffle
          </button>
          <button
            className="rounded-md border bg-background px-4 py-2 text-sm font-medium hover:bg-accent"
            onClick={onReset}
            disabled={busy}
          >
            Reset
          </button>
        </div>
      </div>
      <div className="grid grid-cols-10 gap-2">
        {batch.map((seed) => (
          <button
            key={seed}
            onClick={() => applyGenerated(seed)}
            disabled={busy}
            className="overflow-hidden rounded-full ring-1 ring-border hover:ring-primary"
          >
            <Avatar seed={seed} style="notionists" size="sm" />
          </button>
        ))}
      </div>
    </section>
  )
}
