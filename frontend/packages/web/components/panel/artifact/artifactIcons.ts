import type { Artifact } from '@cubebox/core'
import type { LucideIcon } from 'lucide-react'
import {
  File, FileText, FileCode, FileJson, FileSpreadsheet,
  FileImage, FileVideo, FileAudio, FileArchive, FileType,
  Globe, Code, Image, Database, Presentation,
} from 'lucide-react'

// ── Filename extension → icon ──────────────────────────────────────────────

const extIcons: Record<string, LucideIcon> = {
  // PDF
  pdf: FileType,
  // Documents
  doc: FileText, docx: FileText, odt: FileText, rtf: FileText,
  // Markdown / text
  md: FileText, markdown: FileText, mdx: FileText, txt: FileText,
  // Presentations
  ppt: Presentation, pptx: Presentation, odp: Presentation, key: Presentation,
  // Spreadsheets
  xls: FileSpreadsheet, xlsx: FileSpreadsheet, ods: FileSpreadsheet, csv: FileSpreadsheet,
  // Code
  js: FileCode, ts: FileCode, jsx: FileCode, tsx: FileCode,
  py: FileCode, rb: FileCode, go: FileCode, rs: FileCode,
  java: FileCode, kt: FileCode, c: FileCode, cpp: FileCode, h: FileCode,
  cs: FileCode, swift: FileCode, sh: FileCode, bash: FileCode,
  html: FileCode, css: FileCode, scss: FileCode, less: FileCode,
  sql: FileCode, yaml: FileCode, yml: FileCode, toml: FileCode,
  xml: FileCode, vue: FileCode, svelte: FileCode,
  // Data
  json: FileJson, jsonl: FileJson,
  // Images
  png: FileImage, jpg: FileImage, jpeg: FileImage, gif: FileImage,
  svg: FileImage, webp: FileImage, bmp: FileImage, ico: FileImage,
  // Video
  mp4: FileVideo, webm: FileVideo, mov: FileVideo, avi: FileVideo, mkv: FileVideo,
  // Audio
  mp3: FileAudio, wav: FileAudio, ogg: FileAudio, flac: FileAudio, aac: FileAudio,
  // Archives
  zip: FileArchive, tar: FileArchive, gz: FileArchive, rar: FileArchive, '7z': FileArchive,
}

// ── Mime type prefix → icon ────────────────────────────────────────────────

const mimeIcons: Record<string, LucideIcon> = {
  'application/pdf': FileType,
  'text/markdown': FileText,
  'text/csv': FileSpreadsheet,
  'application/json': FileJson,
}

const mimePrefixIcons: [string, LucideIcon][] = [
  ['image/', FileImage],
  ['video/', FileVideo],
  ['audio/', FileAudio],
  ['text/', FileText],
]

// ── Artifact type → fallback icon ──────────────────────────────────────────

const typeIcons: Record<string, LucideIcon> = {
  website: Globe,
  document: FileText,
  code: Code,
  image: Image,
  data: Database,
  file: File,
}

// ── Label mapping ──────────────────────────────────────────────────────────

const extLabels: Record<string, string> = {
  pdf: 'PDF',
  doc: 'Word', docx: 'Word', odt: 'Document', rtf: 'Document',
  ppt: 'Slides', pptx: 'Slides', odp: 'Slides', key: 'Slides',
  xls: 'Spreadsheet', xlsx: 'Spreadsheet', ods: 'Spreadsheet', csv: 'CSV',
  json: 'JSON', jsonl: 'JSON Lines',
  md: 'Markdown', markdown: 'Markdown', mdx: 'MDX',
  zip: 'Archive', tar: 'Archive', gz: 'Archive', rar: 'Archive', '7z': 'Archive',
  mp4: 'Video', webm: 'Video', mov: 'Video',
  mp3: 'Audio', wav: 'Audio', ogg: 'Audio',
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
export function getArtifactIcon(artifact: Artifact): LucideIcon {
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
  return typeIcons[artifact.artifact_type] ?? File
}

/** Return a human-readable label for the artifact's file type. */
export function getArtifactLabel(artifact: Artifact): string {
  const ext = getExt(artifact)
  if (ext && extLabels[ext]) return extLabels[ext]
  return typeLabels[artifact.artifact_type] ?? 'File'
}
