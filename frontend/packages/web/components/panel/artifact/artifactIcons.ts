import type { Artifact } from '@cubebox/core'
import type { IconType } from 'react-icons'
import { FaCode, FaDatabase, FaFile, FaFileLines, FaGlobe, FaImage } from 'react-icons/fa6'
import { getFileFamily, getFileVisual } from '@/lib/fileIcons'

const TYPE_ICONS: Record<string, IconType> = {
  website: FaGlobe,
  document: FaFileLines,
  code: FaCode,
  image: FaImage,
  data: FaDatabase,
  file: FaFile,
}

const TYPE_LABELS: Record<string, string> = {
  website: 'Website',
  document: 'Document',
  code: 'Code',
  image: 'Image',
  data: 'Data',
  file: 'File',
}

function artifactInput(artifact: Artifact): { filename?: string; mime_type?: string } {
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''
  return { filename, mime_type: artifact.mime_type ?? undefined }
}

export function getArtifactIcon(artifact: Artifact): IconType {
  const family = getFileFamily(artifactInput(artifact))
  if (family !== 'unknown') return getFileVisual(artifactInput(artifact)).Icon
  return TYPE_ICONS[artifact.artifact_type] ?? FaFile
}

export function getArtifactLabel(artifact: Artifact): string {
  const visual = getFileVisual(artifactInput(artifact))
  if (visual.family !== 'unknown') return visual.label
  return TYPE_LABELS[artifact.artifact_type] ?? 'File'
}
