'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, postSetup } from '@cubebox/core'
import {
  SLUG_MAX,
  SLUG_MIN,
  slugErrorMessage,
  suggestSlug,
  validateSlug,
  type SlugError,
} from '@/lib/slugRules'

export function SetupForm() {
  const router = useRouter()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!slugTouched) setSlug(suggestSlug(name))
  }, [name, slugTouched])

  const slugError: SlugError | null = slug.length === 0 ? null : validateSlug(slug)
  const nameValid = name.trim().length >= 2 && name.trim().length <= 64
  const canSubmit = nameValid && slug.length >= SLUG_MIN && !slugError && !submitting

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const client = createApiClient('')
      await postSetup(client, { org_name: name.trim(), slug })
      router.replace('/')
    } catch (err) {
      const msg = (err as Error).message
      if (msg.includes('slug_taken')) {
        setError(slugErrorMessage('slug_taken'))
      } else if (msg.includes('slug_invalid_format') || msg.includes('slug_too_short')) {
        setError(slugErrorMessage(msg as SlugError))
      } else if (msg.includes('setup_already_completed')) {
        router.replace('/')
      } else {
        setError(msg || 'Setup failed.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4 w-full max-w-md">
      <div>
        <label htmlFor="org_name" className="block text-sm font-medium">
          Organization name
        </label>
        <input
          id="org_name"
          type="text"
          required
          minLength={2}
          maxLength={64}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          placeholder="e.g. Acme Corp"
        />
      </div>
      <div>
        <label htmlFor="slug" className="block text-sm font-medium">
          Identifier
        </label>
        <input
          id="slug"
          type="text"
          required
          minLength={SLUG_MIN}
          maxLength={SLUG_MAX}
          value={slug}
          onChange={(e) => {
            setSlug(e.target.value)
            setSlugTouched(true)
          }}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
        />
        {slug.length > 0 && slugError && (
          <p className="mt-1 text-xs text-destructive">{slugErrorMessage(slugError)}</p>
        )}
      </div>
      {error && <div className="text-sm text-destructive">{error}</div>}
      <button
        type="submit"
        disabled={!canSubmit}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? 'Creating…' : 'Create organization'}
      </button>
    </form>
  )
}
