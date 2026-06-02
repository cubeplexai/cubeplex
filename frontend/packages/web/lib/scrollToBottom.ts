/**
 * Returns a scheduler that coalesces multiple "scroll to bottom" requests into
 * a single write per animation frame. Both `getElement` and `shouldRun` are
 * called inside the rAF callback — the getter reads the element's latest
 * `scrollHeight` just before the write (avoiding stale heights when many
 * deltas land in one task), and the predicate gets a chance to bail if the
 * user scrolled away between the caller-side decision and the frame firing
 * (`stickToBottom` may have flipped to false in that window). Without the
 * predicate, high-frequency streaming would snap the user back to the bottom
 * after every scroll-up attempt.
 */
export function rafThrottleScrollToBottom(
  getElement: () => HTMLElement | null,
  shouldRun?: () => boolean,
): () => void {
  let pending = false
  return () => {
    if (pending) return
    pending = true
    requestAnimationFrame(() => {
      pending = false
      if (shouldRun && !shouldRun()) return
      const el = getElement()
      if (!el) return
      el.scrollTop = el.scrollHeight
    })
  }
}
