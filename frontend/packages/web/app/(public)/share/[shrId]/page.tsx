'use client'

import { use, useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  getPublicShare,
  type PublicShare,
  type PublicShareArtifact,
  type Message,
} from '@cubebox/core'

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SharedMessage({ message }: { message: Message }) {
  const role = message.role

  const textContent = message.content
    .filter((b): b is Extract<typeof b, { type: 'text' }> => b.type === 'text')
    .map((b) => (b as { type: 'text'; text: string }).text)
    .join('')

  if (role === 'user') {
    return (
      <div className="flex justify-end mb-3">
        <div className="max-w-[75%] rounded-2xl px-4 py-2.5 bg-primary/10 text-foreground text-sm leading-relaxed whitespace-pre-wrap">
          {textContent}
        </div>
      </div>
    )
  }

  if (role === 'tool_result') {
    const toolMsg = message as Extract<Message, { role: 'tool_result' }>
    return (
      <div className="flex justify-start mb-3">
        <div className="max-w-[75%] rounded-xl px-3 py-2 bg-muted text-muted-foreground text-xs font-mono leading-snug">
          <span className="text-xs font-semibold text-foreground/60 mr-2">
            [{toolMsg.tool_name}]
          </span>
          <span className="truncate">
            {textContent.slice(0, 200)}
            {textContent.length > 200 ? '…' : ''}
          </span>
        </div>
      </div>
    )
  }

  // assistant
  return (
    <div className="flex justify-start mb-3">
      <div className="max-w-[80%] rounded-2xl px-4 py-2.5 bg-muted text-foreground text-sm leading-relaxed whitespace-pre-wrap">
        {textContent}
      </div>
    </div>
  )
}

function SharedArtifact({ artifact, shareId }: { artifact: PublicShareArtifact; shareId: string }) {
  const filename = artifact.entry_file ?? artifact.path.split('/').pop()
  const href = filename
    ? `/api/v1/public/shares/${shareId}/artifacts/${artifact.id}/v${artifact.version}/${filename}`
    : null

  const Tag = href ? 'a' : 'div'
  const linkProps = href ? { href, target: '_blank' as const, rel: 'noopener noreferrer' } : {}

  return (
    <Tag
      {...linkProps}
      className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 hover:bg-muted/50 transition-colors group"
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-foreground truncate group-hover:text-primary transition-colors">
          {artifact.name}
        </p>
        {artifact.description && (
          <p className="text-xs text-muted-foreground mt-0.5 truncate">{artifact.description}</p>
        )}
        <p className="text-xs text-muted-foreground/60 mt-0.5">
          {artifact.mime_type ?? artifact.artifact_type} · v{artifact.version}
        </p>
      </div>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="shrink-0 text-muted-foreground group-hover:text-primary transition-colors"
        aria-hidden="true"
      >
        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
        <polyline points="15 3 21 3 21 9" />
        <line x1="10" y1="14" x2="21" y2="3" />
      </svg>
    </Tag>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SharePage({ params }: { params: Promise<{ shrId: string }> }) {
  const { shrId } = use(params)
  const t = useTranslations('publicShare')
  const tTime = useTranslations('time')

  const [share, setShare] = useState<PublicShare | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getPublicShare(shrId)
      .then((data) => {
        if (!cancelled) {
          setShare(data)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Unknown error')
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [shrId])

  function formatDate(iso: string): string {
    const d = new Date(iso)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffMins = Math.floor(diffMs / 60_000)
    const diffHours = Math.floor(diffMs / 3_600_000)
    const diffDays = Math.floor(diffMs / 86_400_000)
    if (diffMins < 1) return tTime('justNow')
    if (diffMins < 60) return tTime('minutesAgo', { n: diffMins })
    if (diffHours < 24) return tTime('hoursAgo', { n: diffHours })
    if (diffDays < 30) return tTime('daysAgo', { n: diffDays })
    return d.toLocaleDateString()
  }

  // Loading state
  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center min-h-screen">
        <div className="w-8 h-8 rounded-full border-2 border-primary border-t-transparent animate-spin" />
      </div>
    )
  }

  // Error / not found state
  if (error || !share) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center min-h-screen gap-3 px-4 text-center">
        <h1 className="text-xl font-semibold text-foreground">{t('notFound')}</h1>
        <p className="text-sm text-muted-foreground max-w-sm">{t('notFoundBody')}</p>
        <p className="text-xs text-muted-foreground/50 mt-4">{t('poweredBy')}</p>
      </div>
    )
  }

  const messages = share.messages as Message[]
  const visibleMessages = messages.filter((m) => !(m.metadata?.synthetic === true))

  return (
    <div className="flex flex-col min-h-screen">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b border-border bg-background/80 backdrop-blur-sm">
        <div className="max-w-3xl mx-auto px-4 h-14 flex items-center justify-between gap-4">
          {/* Logo */}
          <span className="font-semibold text-foreground tracking-tight select-none">cubebox</span>

          {/* Title */}
          <h1 className="flex-1 text-center text-sm font-medium text-foreground truncate px-2">
            {share.title}
          </h1>

          {/* Shared by */}
          <div className="text-right shrink-0">
            <p className="text-xs text-muted-foreground">
              {t('sharedBy', { name: share.creator_display_name })}
            </p>
            <p className="text-xs text-muted-foreground/60">{formatDate(share.created_at)}</p>
          </div>
        </div>
      </header>

      {/* Messages */}
      <main className="flex-1 max-w-3xl w-full mx-auto px-4 py-6">
        <div className="space-y-1">
          {visibleMessages.map((msg, idx) => (
            <SharedMessage key={msg.id ?? idx} message={msg} />
          ))}
        </div>

        {/* Artifacts */}
        {share.artifacts.length > 0 && (
          <section className="mt-8 pt-6 border-t border-border">
            <h2 className="text-sm font-semibold text-foreground mb-3">
              Artifacts ({share.artifacts.length})
            </h2>
            <div className="space-y-2">
              {share.artifacts.map((artifact) => (
                <SharedArtifact key={artifact.id} artifact={artifact} shareId={share.id} />
              ))}
            </div>
          </section>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-border py-4 text-center">
        <p className="text-xs text-muted-foreground/50">{t('poweredBy')}</p>
      </footer>
    </div>
  )
}
