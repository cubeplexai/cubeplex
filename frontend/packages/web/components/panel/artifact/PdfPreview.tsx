'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import {
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  List,
  ChevronRight as Chevron,
} from 'lucide-react'
import type { Artifact } from '@cubebox/core'
import { PreviewLoading } from './PreviewLoading'
import { buildPreviewUrl } from './previewUtils'

import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'

pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'

// ── Types ──────────────────────────────────────────────────────────────────

/** Subset of PDFDocumentProxy used for outline resolution. */
interface PdfProxy {
  numPages: number
  getOutline(): Promise<PdfOutlineItem[] | null>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  getDestination(id: string): Promise<any[] | null>
  getPageIndex(ref: unknown): Promise<number>
}

interface PdfOutlineItem {
  title: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  dest: string | any[] | null
  items: PdfOutlineItem[]
}

interface TocEntry {
  title: string
  page: number // 1-based
  depth: number
  children: TocEntry[]
}

// ── Outline resolution ─────────────────────────────────────────────────────

async function resolveOutline(
  pdf: PdfProxy,
  items: PdfOutlineItem[],
  depth: number,
): Promise<TocEntry[]> {
  const entries: TocEntry[] = []
  for (const item of items) {
    let page = 1
    try {
      let dest = item.dest
      if (typeof dest === 'string') {
        dest = await pdf.getDestination(dest)
      }
      if (Array.isArray(dest) && dest[0]) {
        const idx = await pdf.getPageIndex(dest[0])
        page = idx + 1
      }
    } catch {
      /* skip unresolvable items */
    }
    const children = item.items?.length ? await resolveOutline(pdf, item.items, depth + 1) : []
    entries.push({ title: item.title, page, depth, children })
  }
  return entries
}

// ── TOC sidebar ────────────────────────────────────────────────────────────

function TocPanel({
  entries,
  currentPage,
  onNavigate,
}: {
  entries: TocEntry[]
  currentPage: number
  onNavigate: (page: number) => void
}) {
  return (
    <nav className="h-full overflow-y-auto py-2 text-xs select-none">
      <TocList entries={entries} currentPage={currentPage} onNavigate={onNavigate} />
    </nav>
  )
}

function TocList({
  entries,
  currentPage,
  onNavigate,
}: {
  entries: TocEntry[]
  currentPage: number
  onNavigate: (page: number) => void
}) {
  return (
    <ul className="space-y-px">
      {entries.map((entry, i) => (
        <TocItem key={i} entry={entry} currentPage={currentPage} onNavigate={onNavigate} />
      ))}
    </ul>
  )
}

function TocItem({
  entry,
  currentPage,
  onNavigate,
}: {
  entry: TocEntry
  currentPage: number
  onNavigate: (page: number) => void
}) {
  const [expanded, setExpanded] = useState(true)
  const hasChildren = entry.children.length > 0
  const isActive = entry.page === currentPage

  return (
    <li>
      <button
        onClick={() => onNavigate(entry.page)}
        className={`group flex items-center gap-1 w-full text-left px-2 py-1 rounded
          transition-colors hover:bg-muted/60
          ${isActive ? 'bg-primary/10 text-primary font-medium' : 'text-foreground/80'}`}
        style={{ paddingLeft: `${entry.depth * 12 + 8}px` }}
      >
        {hasChildren && (
          <span
            onClick={(e) => {
              e.stopPropagation()
              setExpanded(!expanded)
            }}
            className="shrink-0 p-0.5 rounded hover:bg-muted"
          >
            <Chevron className={`size-3 transition-transform ${expanded ? 'rotate-90' : ''}`} />
          </span>
        )}
        <span className="truncate flex-1">{entry.title}</span>
        <span className="shrink-0 text-[10px] text-muted-foreground/60 tabular-nums">
          {entry.page}
        </span>
      </button>
      {hasChildren && expanded && (
        <TocList entries={entry.children} currentPage={currentPage} onNavigate={onNavigate} />
      )}
    </li>
  )
}

// ── PDF Preview ────────────────────────────────────────────────────────────

interface PdfPreviewProps {
  artifact: Artifact
  version: number | null
  workspaceId: string
}

export function PdfPreview({ artifact, version, workspaceId }: PdfPreviewProps) {
  const [loading, setLoading] = useState(true)
  const [numPages, setNumPages] = useState(0)
  const [currentPage, setCurrentPage] = useState(1)
  const [scale, setScale] = useState(1.0)
  const [containerWidth, setContainerWidth] = useState(0)
  const [tocEntries, setTocEntries] = useState<TocEntry[]>([])
  const [tocOpen, setTocOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map())

  const filename = artifact.entry_file || artifact.path.split('/').pop() || 'file.pdf'
  const fileUrl = buildPreviewUrl(artifact, filename, version, workspaceId)

  // Track container width for responsive page sizing
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0
      if (width > 0) setContainerWidth(width)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // Track which page is currently visible via scroll position
  useEffect(() => {
    const scrollEl = scrollRef.current
    if (!scrollEl || numPages === 0) return
    const handleScroll = () => {
      const scrollMid = scrollEl.scrollTop + scrollEl.clientHeight / 3
      let closest = 1
      let closestDist = Infinity
      pageRefs.current.forEach((el, page) => {
        const dist = Math.abs(el.offsetTop - scrollMid)
        if (dist < closestDist) {
          closestDist = dist
          closest = page
        }
      })
      setCurrentPage(closest)
    }
    scrollEl.addEventListener('scroll', handleScroll, { passive: true })
    return () => scrollEl.removeEventListener('scroll', handleScroll)
  }, [numPages])

  const onDocumentLoadSuccess = useCallback(async (pdf: PdfProxy) => {
    setNumPages(pdf.numPages)
    setCurrentPage(1)
    setLoading(false)

    // Extract outline (TOC)
    const outline = await pdf.getOutline()
    if (outline?.length) {
      const entries = await resolveOutline(pdf, outline as PdfOutlineItem[], 0)
      setTocEntries(entries)
    } else {
      setTocEntries([])
    }
  }, [])

  const scrollToPage = useCallback((page: number) => {
    const el = pageRefs.current.get(page)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  // Page width: fit container minus padding (and minus TOC width when open), then apply scale
  const tocWidth = tocOpen && tocEntries.length > 0 ? 220 : 0
  const baseWidth = containerWidth > 0 ? containerWidth - 48 - tocWidth : 0
  const pageWidth = baseWidth > 0 ? baseWidth * scale : undefined

  const hasToc = tocEntries.length > 0

  return (
    <div ref={containerRef} className="flex flex-col h-full">
      {/* Toolbar */}
      <div
        className="flex items-center justify-between px-3 py-1.5 border-b border-border
        bg-muted/30 shrink-0"
      >
        <div className="flex items-center gap-1">
          {hasToc && (
            <button
              onClick={() => setTocOpen((v) => !v)}
              className={`p-1 rounded transition-colors
                ${tocOpen ? 'bg-primary/10 text-primary' : 'hover:bg-muted text-foreground'}`}
              title="Table of contents"
            >
              <List className="size-4" />
            </button>
          )}
          <button
            onClick={() => {
              const p = Math.max(1, currentPage - 1)
              scrollToPage(p)
            }}
            disabled={currentPage <= 1}
            className="p-1 rounded hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed
              transition-colors"
          >
            <ChevronLeft className="size-4 text-foreground" />
          </button>
          <span className="text-xs text-muted-foreground tabular-nums min-w-[4rem] text-center">
            {numPages > 0 ? `${currentPage} / ${numPages}` : '-'}
          </span>
          <button
            onClick={() => {
              const p = Math.min(numPages, currentPage + 1)
              scrollToPage(p)
            }}
            disabled={currentPage >= numPages}
            className="p-1 rounded hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed
              transition-colors"
          >
            <ChevronRight className="size-4 text-foreground" />
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setScale((s) => Math.max(0.5, +(s - 0.25).toFixed(2)))}
            disabled={scale <= 0.5}
            className="p-1 rounded hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed
              transition-colors"
          >
            <ZoomOut className="size-4 text-foreground" />
          </button>
          <span className="text-xs text-muted-foreground tabular-nums min-w-[3rem] text-center">
            {Math.round(scale * 100)}%
          </span>
          <button
            onClick={() => setScale((s) => Math.min(3, +(s + 0.25).toFixed(2)))}
            disabled={scale >= 3}
            className="p-1 rounded hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed
              transition-colors"
          >
            <ZoomIn className="size-4 text-foreground" />
          </button>
        </div>
      </div>

      {/* Body: optional TOC sidebar + PDF pages */}
      <div className="relative flex flex-1 overflow-hidden">
        {/* Loading overlay */}
        {loading && (
          <div className="absolute inset-0 z-10 bg-background">
            <PreviewLoading />
          </div>
        )}

        {/* TOC sidebar */}
        {tocOpen && hasToc && (
          <div className="w-[220px] shrink-0 border-r border-border bg-card overflow-hidden">
            <TocPanel
              entries={tocEntries}
              currentPage={currentPage}
              onNavigate={(page) => {
                scrollToPage(page)
              }}
            />
          </div>
        )}

        {/* PDF pages — continuous scroll */}
        <div ref={scrollRef} className="flex-1 overflow-auto bg-muted/40">
          <Document
            file={fileUrl}
            onLoadSuccess={onDocumentLoadSuccess}
            error={
              <div className="p-4 text-sm text-destructive text-center">Failed to load PDF</div>
            }
          >
            <div className="flex flex-col items-center py-4 gap-4">
              {Array.from({ length: numPages }, (_, i) => i + 1).map((page) => (
                <div
                  key={page}
                  ref={(el) => {
                    if (el) pageRefs.current.set(page, el)
                    else pageRefs.current.delete(page)
                  }}
                  className="bg-white rounded shadow-md ring-1 ring-black/5"
                >
                  <Page
                    pageNumber={page}
                    width={pageWidth}
                    loading={
                      <div
                        className="flex items-center justify-center bg-white"
                        style={{ width: pageWidth ?? 'auto', minHeight: 200 }}
                      >
                        <PreviewLoading />
                      </div>
                    }
                  />
                </div>
              ))}
            </div>
          </Document>
        </div>
      </div>
    </div>
  )
}
