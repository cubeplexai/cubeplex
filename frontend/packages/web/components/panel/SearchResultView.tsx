interface SearchItem {
  title: string
  url: string
  snippet: string
}

interface SearchResultViewProps {
  result: string | null
}

function parseSearchResults(
  raw: string,
): SearchItem[] {
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) {
      return parsed.map(
        (item: Record<string, unknown>) => ({
          title: String(item.title ?? ''),
          url: String(item.url ?? item.link ?? ''),
          snippet: String(
            item.snippet ?? item.description ?? '',
          ),
        }),
      )
    }
    if (
      parsed.results &&
      Array.isArray(parsed.results)
    ) {
      return parseSearchResults(
        JSON.stringify(parsed.results),
      )
    }
  } catch {
    // Not JSON — fall through
  }
  return []
}

function getDomain(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}

export function SearchResultView({
  result,
}: SearchResultViewProps) {
  const items = result
    ? parseSearchResults(result)
    : []

  if (items.length === 0 && result) {
    return (
      <div className="p-4">
        <pre
          className="font-mono text-sm text-foreground
            whitespace-pre-wrap"
        >
          {result}
        </pre>
      </div>
    )
  }

  return (
    <div className="p-4 space-y-3">
      <div className="text-sm text-muted-foreground">
        {items.length} results
      </div>
      {items.map((item, i) => (
        <a
          key={i}
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block bg-muted/30 rounded-lg p-3
            hover:bg-muted/50 transition-colors"
        >
          <div
            className="font-medium text-sm
              text-foreground"
          >
            {item.title}
          </div>
          {item.url && (
            <div
              className="text-xs text-muted-foreground
                mt-0.5"
            >
              {getDomain(item.url)}
            </div>
          )}
          {item.snippet && (
            <div
              className="text-sm text-foreground/80
                mt-1 line-clamp-2"
            >
              {item.snippet}
            </div>
          )}
        </a>
      ))}
    </div>
  )
}
