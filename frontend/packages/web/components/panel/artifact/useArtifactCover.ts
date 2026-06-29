'use client'

import { useState, useEffect } from 'react'
import type { Artifact } from '@cubebox/core'
import { buildPreviewUrl, hasImageExt } from './previewUtils'

interface FilesResponse {
  version: number
  files: string[]
}

interface CoverState {
  coverUrl: string | null
  count: number
  loading: boolean
}

export function useArtifactCover(artifact: Artifact, workspaceId: string): CoverState {
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''
  const { id, conversation_id, version: artifactVersion } = artifact

  // Hooks FIRST — before any early return (rules-of-hooks).
  const [state, setState] = useState<CoverState>({
    coverUrl: null,
    count: 0,
    loading: true,
  })

  useEffect(() => {
    if (hasImageExt(filename)) return
    let cancelled = false
    const url =
      `/api/v1/ws/${workspaceId}/conversations/${conversation_id}` +
      `/artifacts/${id}/files?filter=image`
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status}`)
        return res.json() as Promise<FilesResponse>
      })
      .then((body) => {
        if (cancelled) return
        const first = body.files[0]
        // Build preview URL inline so the effect doesn't reference the full
        // artifact object, keeping the dep array narrow and clean.
        const v = artifactVersion
        const coverUrl = first
          ? `/api/v1/ws/${workspaceId}/conversations/${conversation_id}` +
            `/artifacts/${id}/preview/v${v}/${first}`
          : null
        setState({
          coverUrl,
          count: body.files.length,
          loading: false,
        })
      })
      .catch(() => {
        if (!cancelled) setState({ coverUrl: null, count: 0, loading: false })
      })
    return () => {
      cancelled = true
    }
  }, [id, conversation_id, artifactVersion, workspaceId, filename])

  // Single image path -> cover is that file, no list call, count 1.
  // (Computed as plain const, not via hook -- hooks are already at the top.)
  if (hasImageExt(filename)) {
    return {
      coverUrl: buildPreviewUrl(artifact, filename, null, workspaceId),
      count: 1,
      loading: false,
    }
  }

  return state
}
