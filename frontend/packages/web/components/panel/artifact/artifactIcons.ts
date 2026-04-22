import type { Artifact } from '@cubebox/core'
import type { IconType } from 'react-icons'
import {
  FaFile,
  FaFileLines,
  FaFileCode,
  FaFileCsv,
  FaFileExcel,
  FaFileImage,
  FaFileVideo,
  FaFileAudio,
  FaFileZipper,
  FaFilePdf,
  FaFileWord,
  FaFilePowerpoint,
  FaCode,
  FaGlobe,
  FaImage,
  FaDatabase,
} from 'react-icons/fa6'

// ── Filename extension → icon ──────────────────────────────────────────────

const extIcons: Record<string, IconType> = {
  // PDF
  pdf: FaFilePdf,
  // Documents
  doc: FaFileWord,
  docx: FaFileWord,
  odt: FaFileLines,
  rtf: FaFileLines,
  // Markdown / text
  md: FaFileLines,
  markdown: FaFileLines,
  mdx: FaFileLines,
  txt: FaFileLines,
  // Presentations
  ppt: FaFilePowerpoint,
  pptx: FaFilePowerpoint,
  odp: FaFilePowerpoint,
  key: FaFilePowerpoint,
  // Spreadsheets
  xls: FaFileExcel,
  xlsx: FaFileExcel,
  ods: FaFileExcel,
  csv: FaFileCsv,
  // Code
  js: FaFileCode,
  ts: FaFileCode,
  jsx: FaFileCode,
  tsx: FaFileCode,
  py: FaFileCode,
  rb: FaFileCode,
  go: FaFileCode,
  rs: FaFileCode,
  java: FaFileCode,
  kt: FaFileCode,
  c: FaFileCode,
  cpp: FaFileCode,
  h: FaFileCode,
  cs: FaFileCode,
  swift: FaFileCode,
  sh: FaFileCode,
  bash: FaFileCode,
  html: FaFileCode,
  css: FaFileCode,
  scss: FaFileCode,
  less: FaFileCode,
  sql: FaFileCode,
  yaml: FaFileCode,
  yml: FaFileCode,
  toml: FaFileCode,
  xml: FaFileCode,
  vue: FaFileCode,
  svelte: FaFileCode,
  // Data
  json: FaFileCode,
  jsonl: FaFileCode,
  // Images
  png: FaFileImage,
  jpg: FaFileImage,
  jpeg: FaFileImage,
  gif: FaFileImage,
  svg: FaFileImage,
  webp: FaFileImage,
  bmp: FaFileImage,
  ico: FaFileImage,
  // Video
  mp4: FaFileVideo,
  webm: FaFileVideo,
  mov: FaFileVideo,
  avi: FaFileVideo,
  mkv: FaFileVideo,
  // Audio
  mp3: FaFileAudio,
  wav: FaFileAudio,
  ogg: FaFileAudio,
  flac: FaFileAudio,
  aac: FaFileAudio,
  // Archives
  zip: FaFileZipper,
  tar: FaFileZipper,
  gz: FaFileZipper,
  rar: FaFileZipper,
  '7z': FaFileZipper,
}

// ── Mime type → icon ──────────────────────────────────────────────────────

const mimeIcons: Record<string, IconType> = {
  'application/pdf': FaFilePdf,
  'text/markdown': FaFileLines,
  'text/csv': FaFileCsv,
  'application/json': FaFileCode,
}

const mimePrefixIcons: [string, IconType][] = [
  ['image/', FaFileImage],
  ['video/', FaFileVideo],
  ['audio/', FaFileAudio],
  ['text/', FaFileLines],
]

// ── Artifact type → fallback icon ──────────────────────────────────────────

const typeIcons: Record<string, IconType> = {
  website: FaGlobe,
  document: FaFileLines,
  code: FaCode,
  image: FaImage,
  data: FaDatabase,
  file: FaFile,
}

// ── Label mapping ──────────────────────────────────────────────────────────

const extLabels: Record<string, string> = {
  pdf: 'PDF',
  doc: 'Word',
  docx: 'Word',
  odt: 'Document',
  rtf: 'Document',
  ppt: 'Slides',
  pptx: 'Slides',
  odp: 'Slides',
  key: 'Slides',
  xls: 'Excel',
  xlsx: 'Excel',
  ods: 'Spreadsheet',
  csv: 'CSV',
  json: 'JSON',
  jsonl: 'JSON Lines',
  md: 'Markdown',
  markdown: 'Markdown',
  mdx: 'MDX',
  zip: 'Archive',
  tar: 'Archive',
  gz: 'Archive',
  rar: 'Archive',
  '7z': 'Archive',
  mp4: 'Video',
  webm: 'Video',
  mov: 'Video',
  mp3: 'Audio',
  wav: 'Audio',
  ogg: 'Audio',
}

const typeLabels: Record<string, string> = {
  website: 'Website',
  document: 'Document',
  code: 'Code',
  image: 'Image',
  data: 'Data',
  file: 'File',
}

// ── Public API ─────────────────────────────────────────────────────────────

function getExt(artifact: Artifact): string {
  const name = artifact.entry_file || artifact.path.split('/').pop() || ''
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/** Return the best icon for a given artifact, considering extension and mime. */
export function getArtifactIcon(artifact: Artifact): IconType {
  // 1. Try extension
  const ext = getExt(artifact)
  if (ext && extIcons[ext]) return extIcons[ext]

  // 2. Try exact mime
  const mime = artifact.mime_type
  if (mime && mimeIcons[mime]) return mimeIcons[mime]

  // 3. Try mime prefix
  if (mime) {
    for (const [prefix, icon] of mimePrefixIcons) {
      if (mime.startsWith(prefix)) return icon
    }
  }

  // 4. Fallback to artifact_type
  return typeIcons[artifact.artifact_type] ?? FaFile
}

/** Return a human-readable label for the artifact's file type. */
export function getArtifactLabel(artifact: Artifact): string {
  const ext = getExt(artifact)
  if (ext && extLabels[ext]) return extLabels[ext]
  return typeLabels[artifact.artifact_type] ?? 'File'
}
