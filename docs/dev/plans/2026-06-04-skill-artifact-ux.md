# Skill Artifact UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three coordinated UX improvements to the skill artifact flow: rename "publish to org marketplace" → "添加到工作空间", add a one-click install button directly on the ArtifactCard, and make publish errors visually prominent.

**Architecture:** Extract publish logic into a shared `usePublishSkill` hook. `ArtifactCard` gains a skill-only install button that uses the hook inline (no dialog). `SkillArtifactPreview` refactors to use the same hook and gets a stronger error banner. No backend changes — the `/ws/{ws}/skills/publish` endpoint already creates a workspace-private install.

**Tech Stack:** React 18, Next.js, lucide-react, next-intl, useSWR (not used here), fetch API.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `frontend/packages/web/hooks/usePublishSkill.ts` | Shared hook: call publish API, manage loading/result state |
| Modify | `frontend/packages/web/components/chat/ArtifactCard.tsx` | Add skill install button using the hook |
| Modify | `frontend/packages/web/components/panel/artifact/SkillArtifactPreview.tsx` | Use hook, improve error banner |
| Modify | `frontend/packages/web/messages/zh.json` | Update 7 i18n keys (adminSkills + chatExtras) |
| Modify | `frontend/packages/web/messages/en.json` | Update 7 i18n keys (adminSkills + chatExtras) |

---

### Task 1: `usePublishSkill` hook

**Files:**
- Create: `frontend/packages/web/hooks/usePublishSkill.ts`

The hook encapsulates the fetch call, loading state, and result. Both `ArtifactCard` and `SkillArtifactPreview` import it. It exposes a `reset()` so callers can clear the result (e.g., after showing a success tick).

- [ ] **Step 1: Create the hook**

```typescript
// frontend/packages/web/hooks/usePublishSkill.ts
'use client'

import { useCallback, useState } from 'react'
import { csrfHeaders, readApiError } from '@/lib/csrf'

export interface PublishResult {
  ok: boolean
  message: string
}

export function usePublishSkill(workspaceId: string, artifactId: string) {
  const [isPublishing, setIsPublishing] = useState(false)
  const [result, setResult] = useState<PublishResult | null>(null)

  const publish = useCallback(async () => {
    setIsPublishing(true)
    setResult(null)
    try {
      const res = await fetch(`/api/v1/ws/${workspaceId}/skills/publish`, {
        method: 'POST',
        credentials: 'include',
        headers: { ...csrfHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ artifact_id: artifactId }),
      })
      if (res.status === 409) {
        setResult({ ok: false, message: 'VERSION_EXISTS' })
        return
      }
      if (!res.ok) {
        setResult({ ok: false, message: await readApiError(res) })
        return
      }
      setResult({ ok: true, message: 'SUCCESS' })
    } finally {
      setIsPublishing(false)
    }
  }, [workspaceId, artifactId])

  const reset = useCallback(() => setResult(null), [])

  return { publish, isPublishing, result, reset }
}
```

Note: the hook returns raw sentinels (`'VERSION_EXISTS'`, `'SUCCESS'`) for structured cases, and the raw error string otherwise. Callers resolve i18n display text using `useTranslations`.

- [ ] **Step 2: Verify TypeScript**

Run from `frontend/packages/web`:
```bash
pnpm run type-check 2>&1 | grep -E "usePublishSkill|error TS" | head -10
```
Expected: no errors for this file.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/hooks/usePublishSkill.ts
git commit -m "feat(skill-ux): add usePublishSkill hook for shared publish logic"
```

---

### Task 2: i18n string updates

**Files:**
- Modify: `frontend/packages/web/messages/zh.json`
- Modify: `frontend/packages/web/messages/en.json`

Update `adminSkills` (7 keys) and `chatExtras` (1 new key) in both files.

- [ ] **Step 1: Update zh.json**

In `adminSkills`, find and replace these key values:

```json
"versionExists": "版本已存在，请在 SKILL.md 中更新 version 后重新添加",
"publishSuccess": "已添加到工作空间",
"publishButton": "添加到工作空间",
"confirmPublishTitle": "添加到工作空间",
"publishDesc": "将这个 skill 添加到当前工作空间，添加后可在对话中通过 load_skill 使用。version 取自 SKILL.md frontmatter，若要更新请在 SKILL.md 里 bump version 后重新添加。",
"publishing": "添加中…",
"confirmPublishBtn": "确认添加"
```

In `chatExtras`, add one new key after `"download"`:

```json
"addToWorkspace": "添加到工作空间"
```

- [ ] **Step 2: Update en.json**

In `adminSkills`, find and replace these key values:

```json
"versionExists": "Version already exists, please update version in SKILL.md before adding again",
"publishSuccess": "Added to workspace",
"publishButton": "Add to workspace",
"confirmPublishTitle": "Add to workspace",
"publishDesc": "Add this skill to the current workspace. Once added it is available via load_skill in conversations. Version is taken from SKILL.md frontmatter; to update, bump version in SKILL.md and add again.",
"publishing": "Adding…",
"confirmPublishBtn": "Confirm add"
```

In `chatExtras`, add one new key after `"download"`:

```json
"addToWorkspace": "Add to workspace"
```

- [ ] **Step 3: Run i18n parity check**

```bash
cd frontend && pnpm -w run lint 2>&1 | grep -i "parity\|i18n" | head -10
```
Expected: no i18n parity errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/messages/zh.json frontend/packages/web/messages/en.json
git commit -m "feat(skill-ux): rename publish strings to 添加到工作空间"
```

---

### Task 3: Refactor `SkillArtifactPreview` to use hook + strengthen error display

**Files:**
- Modify: `frontend/packages/web/components/panel/artifact/SkillArtifactPreview.tsx`

Replace the inline `useState` + `handlePublish` logic with `usePublishSkill`. Improve the error banner: add `AlertCircle` icon, left border, and stronger styling so it's visually distinct from content.

- [ ] **Step 1: Rewrite `SkillArtifactPreview.tsx`**

Replace the entire file with:

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X, AlertCircle, CheckCircle2 } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import useSWR from 'swr'
import type { Artifact } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { cn, proseClasses } from '@/lib/utils'
import { usePublishSkill } from '@/hooks/usePublishSkill'
import { buildPreviewUrl } from './previewUtils'

async function fetchText(url: string): Promise<string> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.text()
}

export function SkillArtifactPreview({
  artifact,
  version,
  workspaceId,
}: {
  artifact: Artifact
  version: number | null
  workspaceId: string
}) {
  const t = useTranslations('adminSkills')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const { publish, isPublishing, result } = usePublishSkill(workspaceId, artifact.id)

  const skillMdUrl = buildPreviewUrl(artifact, 'SKILL.md', version, workspaceId)
  const { data: skillMd, isLoading } = useSWR<string>(skillMdUrl, fetchText, {
    revalidateOnFocus: false,
  })

  async function handleConfirmPublish(): Promise<void> {
    await publish()
    setConfirmOpen(false)
  }

  const resultMessage =
    result?.message === 'VERSION_EXISTS'
      ? t('versionExists')
      : result?.message === 'SUCCESS'
        ? t('publishSuccess')
        : (result?.message ?? '')

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
        <header className="flex flex-wrap items-baseline gap-2">
          <span className="font-mono font-semibold">{artifact.name}</span>
          <span className="text-xs text-muted-foreground">entry: SKILL.md</span>
          <span className="text-xs text-muted-foreground">v{artifact.version}</span>
        </header>

        {result && (
          <div
            className={cn(
              'flex items-start gap-2 rounded-md border-l-4 px-3 py-2.5 text-sm font-medium',
              result.ok
                ? 'border-green-500 bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300'
                : 'border-destructive bg-destructive/10 text-destructive',
            )}
          >
            {result.ok ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
            ) : (
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
            )}
            <span>{resultMessage}</span>
          </div>
        )}

        <div className={proseClasses}>
          {isLoading ? (
            <p className="text-sm text-muted-foreground">{t('previewLoading')}</p>
          ) : skillMd ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{skillMd}</ReactMarkdown>
          ) : (
            <p className="text-sm text-muted-foreground">{t('noSkillMd')}</p>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t p-4">
        <Button size="sm" onClick={() => setConfirmOpen(true)} disabled={!!result?.ok}>
          {t('publishButton')}
        </Button>
      </div>

      <DialogPrimitive.Root open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200" />
          <DialogPrimitive.Popup
            className={cn(
              'fixed left-1/2 top-1/2 z-50 w-[min(480px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2',
              'rounded-xl border border-border bg-popover p-5 text-popover-foreground shadow-2xl',
              'data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 transition-opacity duration-200',
            )}
          >
            <div className="flex items-start justify-between gap-3">
              <DialogPrimitive.Title className="text-base font-semibold">
                {t('confirmPublishTitle')}
              </DialogPrimitive.Title>
              <DialogPrimitive.Close
                render={
                  <button
                    type="button"
                    aria-label="close"
                    className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <X className="size-4" />
                  </button>
                }
              />
            </div>
            <p className="mt-3 text-sm text-muted-foreground">{t('publishDesc')}</p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmOpen(false)}
                disabled={isPublishing}
              >
                {t('cancel')}
              </Button>
              <Button size="sm" onClick={() => void handleConfirmPublish()} disabled={isPublishing}>
                {isPublishing ? t('publishing') : t('confirmPublishBtn')}
              </Button>
            </div>
          </DialogPrimitive.Popup>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend/packages/web && pnpm run type-check 2>&1 | grep "SkillArtifactPreview\|error TS" | head -10
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/panel/artifact/SkillArtifactPreview.tsx
git commit -m "feat(skill-ux): use usePublishSkill hook; strengthen error banner with icon + border"
```

---

### Task 4: Add install button to `ArtifactCard` for skill artifacts

**Files:**
- Modify: `frontend/packages/web/components/chat/ArtifactCard.tsx`

Add a `PackagePlus` button that only renders when `artifact.artifact_type === 'skill'`. It calls `usePublishSkill` inline with no dialog. States:
- **Idle**: `PackagePlus` icon, title = `t('addToWorkspace')`
- **Loading**: `Loader2` spinning icon, button disabled
- **Success**: `Check` icon in green for 1.5s, then button disabled (already added)
- **Error**: `AlertCircle` icon in red, title = error message (tooltip on hover)

The hook instance lives inside `ArtifactCard` so each card has its own state.

- [ ] **Step 1: Rewrite `ArtifactCard.tsx`**

```tsx
'use client'

import { memo, useCallback, useEffect } from 'react'
import { Download, Package, Eye, PackagePlus, Loader2, Check, AlertCircle } from 'lucide-react'
import type { Artifact } from '@cubebox/core'
import { usePanelStore } from '@cubebox/core'
import { useTranslations } from 'next-intl'

import { getArtifactIcon, getArtifactLabel } from '@/components/panel/artifact/artifactIcons'
import { buildDownloadUrl } from '@/components/panel/artifact/previewUtils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { usePublishSkill } from '@/hooks/usePublishSkill'
import { cn } from '@/lib/utils'

interface ArtifactCardProps {
  artifact: Artifact
}

function SkillInstallButton({
  workspaceId,
  artifactId,
  label,
}: {
  workspaceId: string
  artifactId: string
  label: string
}) {
  const { publish, isPublishing, result, reset } = usePublishSkill(workspaceId, artifactId)

  // Auto-reset success state after 1.5s
  useEffect(() => {
    if (result?.ok) {
      const t = setTimeout(reset, 1500)
      return () => clearTimeout(t)
    }
  }, [result, reset])

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      void publish()
    },
    [publish],
  )

  if (isPublishing) {
    return (
      <button
        disabled
        className="flex size-8 items-center justify-center rounded-md text-muted-foreground"
      >
        <Loader2 className="size-4 animate-spin" />
      </button>
    )
  }

  if (result?.ok) {
    return (
      <button
        disabled
        className="flex size-8 items-center justify-center rounded-md text-green-600 dark:text-green-400"
      >
        <Check className="size-4" />
      </button>
    )
  }

  if (result && !result.ok) {
    const errMsg = result.message === 'VERSION_EXISTS' ? label + ' (version exists)' : result.message
    return (
      <button
        onClick={handleClick}
        title={errMsg}
        className="flex size-8 items-center justify-center rounded-md text-destructive transition-colors hover:bg-destructive/10"
      >
        <AlertCircle className="size-4" />
      </button>
    )
  }

  return (
    <button
      onClick={handleClick}
      title={label}
      className={cn(
        'flex size-8 items-center justify-center rounded-md',
        'text-muted-foreground transition-colors hover:bg-muted hover:text-foreground',
      )}
    >
      <PackagePlus className="size-4" />
    </button>
  )
}

export const ArtifactCard = memo(function ArtifactCard({ artifact }: ArtifactCardProps) {
  const t = useTranslations('chatExtras')
  const Icon = getArtifactIcon(artifact)
  const label = getArtifactLabel(artifact)
  const openPreview = usePanelStore((s) => s.openArtifact)
  const { workspaceId } = useWorkspaceContext()

  const downloadUrl = workspaceId ? buildDownloadUrl(artifact, workspaceId) : '#'

  const handlePreview = useCallback(() => {
    openPreview(artifact.conversation_id, artifact.id)
  }, [openPreview, artifact.conversation_id, artifact.id])

  return (
    <div
      className="my-2 rounded-lg border border-border bg-card p-3 cursor-pointer
        transition-colors hover:border-primary/30 hover:bg-card/80"
      onClick={handlePreview}
    >
      <div className="flex items-center gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10">
          <Icon className="size-4 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground">{artifact.name}</span>
            {artifact.version > 1 && (
              <span
                className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px]
                text-muted-foreground"
              >
                v{artifact.version}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Package className="size-3" />
            <span>{label}</span>
            {artifact.description && (
              <>
                <span className="text-muted-foreground/40">|</span>
                <span className="truncate">{artifact.description}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={(e) => {
              e.stopPropagation()
              handlePreview()
            }}
            className="flex size-8 items-center justify-center rounded-md
              text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title={t('preview')}
          >
            <Eye className="size-4" />
          </button>
          <a
            href={downloadUrl}
            onClick={(e) => e.stopPropagation()}
            className="flex size-8 items-center justify-center rounded-md
              text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title={t('download')}
          >
            <Download className="size-4" />
          </a>
          {artifact.artifact_type === 'skill' && workspaceId && (
            <SkillInstallButton
              workspaceId={workspaceId}
              artifactId={artifact.id}
              label={t('addToWorkspace')}
            />
          )}
        </div>
      </div>
    </div>
  )
})
```

- [ ] **Step 2: Type-check**

```bash
cd frontend/packages/web && pnpm run type-check 2>&1 | grep "ArtifactCard\|error TS" | head -10
```
Expected: no errors.

- [ ] **Step 3: Lint**

```bash
cd frontend/packages/web && pnpm run lint 2>&1 | grep "ArtifactCard\|error" | head -10
```
Expected: no errors (1 pre-existing warning about react-hooks/set-state-in-effect is unrelated).

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/chat/ArtifactCard.tsx
git commit -m "feat(skill-ux): add one-click install button on skill ArtifactCard"
```

---

### Task 5: Full frontend CI check

**Files:** none (verification)

- [ ] **Step 1: Full type-check + lint + build**

```bash
cd frontend && pnpm -w run build-core 2>&1 | tail -5
cd frontend/packages/web && pnpm run type-check 2>&1 | tail -5
cd frontend/packages/web && pnpm run lint 2>&1 | tail -5
```
Expected: all clean (1 pre-existing warning ok).

- [ ] **Step 2: Vitest**

```bash
cd frontend/packages/web && pnpm run test 2>&1 | tail -10
```
Expected: all pass or pre-existing failures only.

- [ ] **Step 3: No additional commit** unless failures found.

---

## Self-Review

**Spec coverage:**
- ✅ Button rename "发布到组织市场" → "添加到工作空间": Task 2 (i18n) + Tasks 3/4 use the new keys
- ✅ No backend changes: confirmed, endpoint already workspace-scoped
- ✅ Install button on ArtifactCard (skill-only): Task 4
- ✅ No confirm dialog on card button (inline, lightweight): Task 4 — `SkillInstallButton` calls `publish()` directly
- ✅ SkillArtifactPreview keeps dialog: Task 3 — dialog preserved, button text updated
- ✅ Error display strengthened: Task 3 — `border-l-4 border-destructive` + `AlertCircle` icon
- ✅ Shared hook: Task 1 — `usePublishSkill` used by both components
- ✅ Success auto-reset on card: Task 4 — `useEffect` clears after 1.5s
- ✅ i18n parity (en + zh): Task 2

**Placeholder scan:** No TBD/TODO. All code blocks are complete and self-contained.

**Type consistency:** `PublishResult { ok: boolean; message: string }` defined in Task 1 and consumed consistently in Tasks 3 and 4. `usePublishSkill(workspaceId, artifactId)` signature identical across all usages. `result.message === 'VERSION_EXISTS'` sentinel string consistent across hook (Task 1), SkillArtifactPreview (Task 3), and SkillInstallButton error tooltip (Task 4).
