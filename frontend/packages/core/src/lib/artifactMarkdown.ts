import type { Artifact } from '../types/artifact'

const MD_EXT = /\.(md|markdown|mdx)$/i
const MD_MIME = new Set(['text/markdown', 'text/x-markdown'])

/** Basename of path, or empty string if none. */
export function artifactBasename(path: string | null | undefined): string {
  if (!path) return ''
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean)
  return parts[parts.length - 1] ?? ''
}

/**
 * Filename used for markdown preview/edit targeting.
 * Prefers ``entry_file``, else basename of ``path``.
 */
export function markdownFilename(artifact: Pick<Artifact, 'path' | 'entry_file'>): string | null {
  const entry = artifact.entry_file?.trim()
  if (entry) {
    // Absolute or parent-escaping entry is not a safe single-file target.
    if (entry.startsWith('/') || entry.includes('..')) return null
    return entry.split('/').filter(Boolean).pop() ?? null
  }
  const base = artifactBasename(artifact.path)
  return base || null
}

/** True when this artifact should render as an inline markdown card. */
export function isMarkdownArtifact(
  artifact: Pick<Artifact, 'artifact_type' | 'path' | 'entry_file' | 'mime_type'>,
): boolean {
  const mime = artifact.mime_type?.toLowerCase().split(';')[0]?.trim()
  if (mime && MD_MIME.has(mime)) return true

  if (artifact.artifact_type !== 'document') return false

  const name = markdownFilename(artifact)
  return name != null && MD_EXT.test(name)
}

/**
 * Whether the artifact is eligible for content edit in v1.
 * Requires markdown eligibility plus a clear single-file target name.
 */
export function isMarkdownEditable(
  artifact: Pick<Artifact, 'artifact_type' | 'path' | 'entry_file' | 'mime_type'>,
): boolean {
  if (!isMarkdownArtifact(artifact)) return false
  const name = markdownFilename(artifact)
  return name != null && MD_EXT.test(name)
}
