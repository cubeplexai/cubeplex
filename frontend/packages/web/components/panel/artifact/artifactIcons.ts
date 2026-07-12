import type { ComponentType } from 'react'
import type { Artifact } from '@cubeplex/core'
import type { IconType } from 'react-icons'
import { FaCode, FaDatabase, FaFile, FaFileLines, FaGlobe, FaImage } from 'react-icons/fa6'
import { Sparkles } from 'lucide-react'
import { getFileFamily, getFileVisual } from '@/lib/fileIcons'

type ArtifactIcon = ComponentType<{ className?: string }>

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
  skill: 'Skill',
  file: 'File',
}

function artifactInput(artifact: Artifact): { filename?: string; mime_type?: string } {
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''
  return { filename, mime_type: artifact.mime_type ?? undefined }
}

export function getArtifactIcon(artifact: Artifact): ArtifactIcon {
  if (artifact.artifact_type === 'skill') return Sparkles
  const family = getFileFamily(artifactInput(artifact))
  if (family !== 'unknown') return getFileVisual(artifactInput(artifact)).Icon
  return TYPE_ICONS[artifact.artifact_type] ?? FaFile
}

export function getArtifactLabel(artifact: Artifact): string {
  if (artifact.artifact_type === 'skill') return TYPE_LABELS.skill
  const visual = getFileVisual(artifactInput(artifact))
  if (visual.family !== 'unknown') return visual.label
  return TYPE_LABELS[artifact.artifact_type] ?? 'File'
}
