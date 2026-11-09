// Next updates the address only after a soft navigation commits. A failed
// flight request, or a route segment that never resolves, leaves the previous
// URL in place while the sidebar highlight has already moved. When that
// happens, load the clicked conversation with a normal browser navigation.
// A later click replaces any wait still pending. Clicking the open
// conversation does not reload it.

export const CONVERSATION_NAV_FALLBACK_MS = 2000

type BrowserLocation = {
  pathname: string
  assign: (href: string) => void
}

let attempt = 0
let timer: ReturnType<typeof setTimeout> | null = null

export function scheduleConversationNavFallback(
  href: string,
  fromPathname: string,
  location: BrowserLocation = window.location,
): void {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
  const id = ++attempt
  if (href === fromPathname) return
  timer = setTimeout(() => {
    if (id !== attempt) return
    timer = null
    if (location.pathname !== fromPathname) return
    location.assign(href)
  }, CONVERSATION_NAV_FALLBACK_MS)
}
