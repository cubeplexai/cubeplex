/**
 * Returns a scheduler that coalesces multiple "scroll to bottom" requests into
 * a single write per animation frame. The supplied getter is called inside the
 * rAF callback so the element's latest scrollHeight is read just before the
 * write — avoiding stale heights when many deltas land in one task.
 */
export function rafThrottleScrollToBottom(getElement: () => HTMLElement | null): () => void {
  let pending = false
  return () => {
    if (pending) return
    pending = true
    requestAnimationFrame(() => {
      pending = false
      const el = getElement()
      if (!el) return
      el.scrollTop = el.scrollHeight
    })
  }
}
