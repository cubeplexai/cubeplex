import { useEffect, useRef } from 'react'
import { Globe, ExternalLink, Search } from 'lucide-react'

interface SearchResult {
  title: string
  url: string
  description: string
  content?: string
  date?: string
}

interface SearchData {
  query: string
  results: SearchResult[]
}

interface SearchResultViewProps {
  result: string | null
  args?: Record<string, unknown>
  highlightText?: string | null
  highlightKey?: number
}

function parseSearchData(raw: string, args?: Record<string, unknown>): SearchData | null {
  try {
    const parsed = JSON.parse(raw)
    // Direct format: { query, results: [...] }
    if (parsed.query && Array.isArray(parsed.results)) {
      return parsed as SearchData
    }
    // Array of results
    if (Array.isArray(parsed)) {
      return {
        query: String(args?.query ?? '') || 'Search',
        results: parsed,
      }
    }
  } catch {
    // Not JSON
  }
  return null
}

function getDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function getFaviconUrl(url: string): string {
  try {
    const origin = new URL(url).origin
    return `${origin}/favicon.ico`
  } catch {
    return ''
  }
}

export function SearchResultView({
  result,
  args,
  highlightText,
  highlightKey,
}: SearchResultViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!highlightText || !containerRef.current) return
    const items = containerRef.current.querySelectorAll('[data-result-item]')
    let matched: Element | undefined
    for (const item of items) {
      if (item.textContent?.includes(highlightText.slice(0, 50))) {
        item.classList.add('ring-2', 'ring-primary/50', 'bg-primary/10')
        item.scrollIntoView({ behavior: 'smooth', block: 'center' })
        matched = item
        break
      }
    }
    return () => {
      matched?.classList.remove('ring-2', 'ring-primary/50', 'bg-primary/10')
    }
  }, [highlightText, highlightKey])

  if (!result) {
    return <div className="p-6 text-sm text-muted-foreground">No results</div>
  }

  const data = parseSearchData(result, args)

  if (!data) {
    return (
      <div className="p-4">
        <pre
          className="font-mono text-sm text-foreground
            whitespace-pre-wrap break-all"
        >
          {result}
        </pre>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Query header */}
      <div
        className="px-4 py-3 border-b border-border
          bg-muted/20"
      >
        <div className="flex items-center gap-2 text-sm">
          <Search className="size-3.5 text-muted-foreground shrink-0" />
          <span className="font-medium text-foreground">{data.query}</span>
          <span className="text-muted-foreground ml-auto shrink-0">
            {data.results.length} results
          </span>
        </div>
      </div>

      {/* Results list */}
      <div ref={containerRef} className="p-3 space-y-1.5">
        {data.results.map((item, i) => (
          <a
            key={i}
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            data-result-item
            className="group flex gap-3 rounded-lg px-3 py-2.5
              hover:bg-muted/40 transition-colors"
          >
            {/* Number */}
            <span
              className="text-xs text-muted-foreground/50
                font-mono mt-0.5 shrink-0 w-4 text-right"
            >
              {i + 1}
            </span>

            {/* Content */}
            <div className="flex-1 min-w-0">
              {/* Domain line */}
              <div className="flex items-center gap-1.5 mb-0.5">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={getFaviconUrl(item.url)}
                  alt=""
                  className="size-3.5 rounded-sm"
                  onError={(e) => {
                    e.currentTarget.style.display = 'none'
                    const next = e.currentTarget.nextElementSibling as HTMLElement | null
                    if (next) next.style.display = ''
                  }}
                />
                <Globe
                  className="size-3.5
                    text-muted-foreground hidden"
                />
                <span
                  className="text-xs
                    text-muted-foreground truncate"
                >
                  {getDomain(item.url)}
                </span>
              </div>

              {/* Title */}
              <div
                className="text-sm font-medium
                  text-foreground
                  group-hover:text-primary
                  transition-colors line-clamp-1"
              >
                {item.title}
              </div>

              {/* Description */}
              {item.description && (
                <div
                  className="text-xs text-muted-foreground
                    mt-0.5 line-clamp-2 leading-relaxed"
                >
                  {item.description}
                </div>
              )}
            </div>

            {/* External link icon */}
            <ExternalLink
              className="size-3 text-muted-foreground/30
                group-hover:text-muted-foreground
                transition-colors shrink-0 mt-1"
            />
          </a>
        ))}
      </div>
    </div>
  )
}
