'use client'

import { use, useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Loader2 } from 'lucide-react'
import {
  createApiClient,
  listWorkspaceArtifacts,
  deleteArtifact,
  useArtifactStore,
  usePanelStore,
} from '@cubebox/core'
import type { Artifact } from '@cubebox/core'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import { ArtifactPanel } from '@/components/panel/artifact/ArtifactPanel'
import { ArtifactsToolbar } from '@/components/artifacts/ArtifactsToolbar'
import { ArtifactLibraryCard } from '@/components/artifacts/ArtifactLibraryCard'
import { ArtifactsEmptyState } from '@/components/artifacts/ArtifactsEmptyState'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { toast } from 'sonner'

interface PageProps {
  params: Promise<{ wsId: string }>
}

export default function WorkspaceArtifactsPage({ params }: PageProps): React.ReactElement {
  const { wsId } = use(params)
  const t = useTranslations('artifactsPage')

  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedType, setSelectedType] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [pendingDelete, setPendingDelete] = useState<Artifact | null>(null)

  const view = usePanelStore((s) => s.view)
  const closePanel = usePanelStore((s) => s.close)
  const seedArtifact = useArtifactStore((s) => s.addOrUpdate)

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    let cancelled = false
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    listWorkspaceArtifacts(client)
      .then(({ artifacts: list }) => {
        if (cancelled) return
        setArtifacts(list)
        for (const a of list) seedArtifact(a.conversation_id, a)
      })
      .catch(() => {
        if (!cancelled) toast.error(t('loadFailed'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client, seedArtifact, t])

  useEffect(() => {
    return () => {
      if (usePanelStore.getState().view.type === 'artifact') {
        usePanelStore.getState().close()
      }
    }
  }, [])

  const types = useMemo(
    () => Array.from(new Set(artifacts.map((a) => a.artifact_type))).sort(),
    [artifacts],
  )

  const filtered = useMemo(
    () =>
      artifacts.filter((a) => {
        if (selectedType && a.artifact_type !== selectedType) return false
        if (search && !a.name.toLowerCase().includes(search.toLowerCase())) return false
        return true
      }),
    [artifacts, selectedType, search],
  )

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete) return
    const target = pendingDelete
    setPendingDelete(null)
    try {
      await deleteArtifact(client, target.id)
      setArtifacts((prev) => prev.filter((a) => a.id !== target.id))
      if (view.type === 'artifact' && view.artifactId === target.id) closePanel()
    } catch {
      toast.error(t('deleteFailed'))
    }
  }, [pendingDelete, client, view, closePanel, t])

  const panelOpen = view.type === 'artifact'

  const grid = (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
      </header>
      <div className="border-b border-border/70 px-6 py-3">
        <ArtifactsToolbar
          types={types}
          selectedType={selectedType}
          onSelectType={setSelectedType}
          search={search}
          onSearch={setSearch}
        />
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {loading ? (
          <div className="flex flex-1 items-center justify-center py-24">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : artifacts.length === 0 ? (
          <ArtifactsEmptyState />
        ) : filtered.length === 0 ? (
          <p className="py-16 text-center text-sm text-muted-foreground">{t('noResults')}</p>
        ) : (
          <div
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
            data-testid="artifacts-grid"
          >
            {filtered.map((a) => (
              <ArtifactLibraryCard
                key={a.id}
                artifact={a}
                workspaceId={wsId}
                onDelete={setPendingDelete}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )

  return (
    <>
      <ResizablePanelGroup orientation="horizontal" className="h-full">
        <ResizablePanel defaultSize={panelOpen ? 55 : 100} minSize={30}>
          {grid}
        </ResizablePanel>
        {panelOpen && (
          <>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={45} minSize={25}>
              <ArtifactPanel />
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>

      <AlertDialog open={pendingDelete !== null} onOpenChange={(o) => !o && setPendingDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('deleteConfirmTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('deleteConfirmBody', { name: pendingDelete?.name ?? '' })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('deleteConfirmCancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              data-testid="artifact-delete-confirm"
            >
              {t('deleteConfirmAction')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
