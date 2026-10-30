import type { ComponentType } from 'react'
import {
  File,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Presentation,
} from 'lucide-react'

export type FileFamily =
  | 'pdf'
  | 'word'
  | 'excel'
  | 'csv'
  | 'ppt'
  | 'markdown'
  | 'text'
  | 'code'
  | 'json'
  | 'image'
  | 'video'
  | 'audio'
  | 'archive'
  | 'unknown'

export type FileIcon = ComponentType<{ className?: string }>

export interface FileVisual {
  family: FileFamily
  Icon: FileIcon
  label: string
  bg: string
  fg: string
}

const FAMILY_VISUALS: Record<FileFamily, Omit<FileVisual, 'family'>> = {
  pdf: { Icon: FileText, label: 'PDF', bg: 'bg-rose-500', fg: 'text-white' },
  word: { Icon: FileText, label: 'Word', bg: 'bg-blue-600', fg: 'text-white' },
  excel: { Icon: FileSpreadsheet, label: 'Excel', bg: 'bg-emerald-600', fg: 'text-white' },
  csv: { Icon: FileSpreadsheet, label: 'CSV', bg: 'bg-emerald-600', fg: 'text-white' },
  ppt: { Icon: Presentation, label: 'Slides', bg: 'bg-orange-500', fg: 'text-white' },
  markdown: { Icon: FileText, label: 'Markdown', bg: 'bg-slate-500', fg: 'text-white' },
  text: { Icon: FileText, label: 'Text', bg: 'bg-slate-500', fg: 'text-white' },
  code: { Icon: FileCode, label: 'Code', bg: 'bg-violet-600', fg: 'text-white' },
  json: { Icon: FileCode, label: 'JSON', bg: 'bg-violet-600', fg: 'text-white' },
  image: { Icon: FileImage, label: 'Image', bg: 'bg-pink-500', fg: 'text-white' },
  video: { Icon: FileVideo, label: 'Video', bg: 'bg-fuchsia-600', fg: 'text-white' },
  audio: { Icon: FileAudio, label: 'Audio', bg: 'bg-cyan-600', fg: 'text-white' },
  archive: { Icon: FileArchive, label: 'Archive', bg: 'bg-amber-600', fg: 'text-white' },
  unknown: { Icon: File, label: 'File', bg: 'bg-zinc-500', fg: 'text-white' },
}

const EXT_TO_FAMILY: Record<string, FileFamily> = {
  pdf: 'pdf',
  doc: 'word',
  docx: 'word',
  odt: 'word',
  rtf: 'word',
  xls: 'excel',
  xlsx: 'excel',
  ods: 'excel',
  csv: 'csv',
  ppt: 'ppt',
  pptx: 'ppt',
  odp: 'ppt',
  key: 'ppt',
  md: 'markdown',
  markdown: 'markdown',
  mdx: 'markdown',
  txt: 'text',
  log: 'text',
  json: 'json',
  jsonl: 'json',
  js: 'code',
  ts: 'code',
  jsx: 'code',
  tsx: 'code',
  mjs: 'code',
  cjs: 'code',
  py: 'code',
  rb: 'code',
  go: 'code',
  rs: 'code',
  java: 'code',
  kt: 'code',
  c: 'code',
  cpp: 'code',
  h: 'code',
  hpp: 'code',
  cs: 'code',
  swift: 'code',
  sh: 'code',
  bash: 'code',
  zsh: 'code',
  html: 'code',
  css: 'code',
  scss: 'code',
  less: 'code',
  sql: 'code',
  yaml: 'code',
  yml: 'code',
  toml: 'code',
  xml: 'code',
  vue: 'code',
  svelte: 'code',
  png: 'image',
  jpg: 'image',
  jpeg: 'image',
  gif: 'image',
  svg: 'image',
  webp: 'image',
  bmp: 'image',
  ico: 'image',
  tiff: 'image',
  mp4: 'video',
  webm: 'video',
  mov: 'video',
  avi: 'video',
  mkv: 'video',
  mp3: 'audio',
  wav: 'audio',
  ogg: 'audio',
  flac: 'audio',
  aac: 'audio',
  zip: 'archive',
  tar: 'archive',
  gz: 'archive',
  rar: 'archive',
  '7z': 'archive',
}

const MIME_TO_FAMILY: Record<string, FileFamily> = {
  'application/pdf': 'pdf',
  'application/msword': 'word',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'word',
  'application/vnd.ms-excel': 'excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'excel',
  'text/csv': 'csv',
  'application/vnd.ms-powerpoint': 'ppt',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'ppt',
  'text/markdown': 'markdown',
  'application/json': 'json',
  'application/x-ndjson': 'json',
  'application/zip': 'archive',
  'application/x-tar': 'archive',
  'application/gzip': 'archive',
  'application/x-7z-compressed': 'archive',
  'application/x-rar-compressed': 'archive',
}

const MIME_PREFIX_TO_FAMILY: [string, FileFamily][] = [
  ['image/', 'image'],
  ['video/', 'video'],
  ['audio/', 'audio'],
  ['text/', 'text'],
]

function getExt(filename: string | undefined): string {
  if (!filename) return ''
  const dot = filename.lastIndexOf('.')
  return dot > 0 ? filename.slice(dot + 1).toLowerCase() : ''
}

export function getFileFamily(input: { filename?: string; mime_type?: string }): FileFamily {
  const ext = getExt(input.filename)
  if (ext && EXT_TO_FAMILY[ext]) return EXT_TO_FAMILY[ext]
  const mime = input.mime_type
  if (mime && MIME_TO_FAMILY[mime]) return MIME_TO_FAMILY[mime]
  if (mime) {
    for (const [prefix, family] of MIME_PREFIX_TO_FAMILY) {
      if (mime.startsWith(prefix)) return family
    }
  }
  return 'unknown'
}

export function getFileVisual(input: { filename?: string; mime_type?: string }): FileVisual {
  const family = getFileFamily(input)
  return { family, ...FAMILY_VISUALS[family] }
}

export function getFileLabel(input: { filename?: string; mime_type?: string }): string {
  return getFileVisual(input).label
}
