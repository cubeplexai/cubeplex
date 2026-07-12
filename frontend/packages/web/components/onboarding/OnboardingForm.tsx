'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { completeOnboarding, createApiClient, useAuthStore } from '@cubeplex/core'
import {
  SLUG_MAX,
  SLUG_MIN,
  slugErrorMessage,
  suggestSlug,
  validateSlug,
  type SlugError,
} from '@/lib/slugRules'

export function OnboardingForm() {
  const t = useTranslations('onboarding')
  const router = useRouter()
  const me = useAuthStore((s) => s.user)

  const fullMode = !me?.org_memberships?.length

  const [orgName, setOrgName] = useState('')
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [workspaceName, setWorkspaceName] = useState('')
  const [workspaceTouched, setWorkspaceTouched] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Auto-suggest slug from org name when slug is untouched
  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    if (fullMode && !slugTouched) setSlug(suggestSlug(orgName))
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [orgName, slugTouched, fullMode])

  // Default the workspace name to a per-user value (display name or email
  // local-part) so multiple personal workspaces in an org are distinguishable
  // — never a uniform "My Workspace". Only fills while the user hasn't edited.
  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    if (workspaceTouched || workspaceName) return
    const name = me?.display_name?.trim() || me?.email?.split('@')[0]
    if (name) setWorkspaceName(t('defaultWorkspaceName', { name }))
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [me, workspaceTouched, workspaceName, t])

  const slugError: SlugError | null = !fullMode || slug.length === 0 ? null : validateSlug(slug)

  const orgNameValid = !fullMode || (orgName.trim().length >= 2 && orgName.trim().length <= 64)
  const slugValid = !fullMode || (slug.length >= SLUG_MIN && !slugError)
  const workspaceNameValid = workspaceName.trim().length >= 1 && workspaceName.trim().length <= 64
  const canSubmit = orgNameValid && slugValid && workspaceNameValid && !submitting

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const client = createApiClient('')
      const body = fullMode
        ? {
            org_name: orgName.trim(),
            org_slug: slug,
            workspace_name: workspaceName.trim(),
          }
        : { workspace_name: workspaceName.trim() }
      const result = await completeOnboarding(client, body)
      router.replace(`/w/${result.workspace_id}`)
    } catch (err) {
      const msg = (err as Error).message
      if (msg.includes('slug_taken')) {
        setError(slugErrorMessage('slug_taken'))
      } else if (msg.includes('onboarding_not_required')) {
        router.replace('/')
      } else {
        setError(msg || 'Onboarding failed.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4 w-full max-w-md">
      <h1 className="text-xl font-semibold">{t('onboardingTitle')}</h1>

      {fullMode && (
        <>
          <div>
            <label htmlFor="org_name" className="block text-sm font-medium">
              {t('orgName')}
            </label>
            <input
              id="org_name"
              type="text"
              required
              minLength={2}
              maxLength={64}
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              placeholder="e.g. Acme Corp"
            />
          </div>
          <div>
            <label htmlFor="slug" className="block text-sm font-medium">
              {t('orgSlug')}
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
        </>
      )}

      <div>
        <label htmlFor="workspace_name" className="block text-sm font-medium">
          {t('workspaceName')}
        </label>
        <input
          id="workspace_name"
          type="text"
          required
          minLength={1}
          maxLength={64}
          value={workspaceName}
          onChange={(e) => {
            setWorkspaceName(e.target.value)
            setWorkspaceTouched(true)
          }}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          placeholder={t('workspaceNamePlaceholder')}
        />
      </div>

      {error && <div className="text-sm text-destructive">{error}</div>}

      <button
        type="submit"
        disabled={!canSubmit}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? t('creating') : fullMode ? t('createOrgAndWorkspace') : t('createWorkspace')}
      </button>
    </form>
  )
}
