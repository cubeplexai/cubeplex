'use client'

import dynamic from 'next/dynamic'
import { useArtifactStore, usePanelStore } from '@cubebox/core'
import type { Artifact } from '@cubebox/core'
import { X, Download, Loader2 } from 'lucide-react'
import { getArtifactIcon } from './artifactIcons'
import { HtmlPreview } from './HtmlPreview'
import { ImagePreview } from './ImagePreview'
import { CodePreview } from './CodePreview'
import { DocumentPreview } from './DocumentPreview'
import { DataPreview } from './DataPreview'
import { FallbackPreview } from './FallbackPreview'

const PdfPreview = dynamic(
  () => import('./PdfPreview').then(m => m.PdfPreview),
  {
    ssr: false,
    loading: () => (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    ),
  },
)

function isPdf(artifact: Artifact): boolean {
  if (artifact.mime_type === 'application/pdf') return true
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''
  return /\.pdf$/i.test(filename)
}

function ArtifactPanelHeader({ artifact, onClose }: { artifact: Artifact; onClose: () => void }) {
  const Icon = getArtifactIcon(artifact)
  const downloadUrl =
    `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/download`

  return (
    <header className="h-11 border-b border-border flex items-center gap-2 px-4 shrink-0 bg-card">
      <Icon className="size-3.5 text-primary shrink-0" />
      <span className="text-sm font-medium text-foreground truncate flex-1">
        {artifact.name}
      </span>
      {artifact.version > 1 && (
        <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px]
          text-muted-foreground">
          v{artifact.version}
        </span>
      )}
      <span className="flex items-center gap-1">
        <a
          href={downloadUrl}
          className="p-1 rounded hover:bg-muted/50 transition-colors"
          title="Download"
        >
          <Download className="size-3.5 text-muted-foreground" />
        </a>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-muted/50 transition-colors"
          title="Close"
        >
          <X className="size-3.5 text-muted-foreground" />
        </button>
      </span>
    </header>
  )
}

function PreviewContent({ artifact }: { artifact: Artifact }) {
  // Route PDFs to PdfPreview regardless of artifact_type
  if (isPdf(artifact)) {
    return <PdfPreview artifact={artifact} />
  }

  switch (artifact.artifact_type) {
    case 'website':
      return <HtmlPreview artifact={artifact} />
    case 'image':
      return <ImagePreview artifact={artifact} />
    case 'code':
      return <CodePreview artifact={artifact} />
    case 'document':
      return <DocumentPreview artifact={artifact} />
    case 'data':
      return <DataPreview artifact={artifact} />
    default:
      return <FallbackPreview artifact={artifact} />
  }
}

export function ArtifactPanel() {
  const view = usePanelStore(s => s.view)
  const close = usePanelStore(s => s.close)
  const artifacts = useArtifactStore(s => s.artifacts)

  if (view.type !== 'artifact') return null

  const artifact = artifacts[view.conversationId]?.[view.artifactId]
  if (!artifact) return null

  return (
    <div className="flex flex-col h-full bg-background">
      <ArtifactPanelHeader artifact={artifact} onClose={close} />
      <div className="flex-1 overflow-hidden">
        <PreviewContent artifact={artifact} />
      </div>
    </div>
  )
}
