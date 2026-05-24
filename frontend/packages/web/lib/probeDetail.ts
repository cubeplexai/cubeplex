// cubepi formats upstream probe failures as a dense, often-truncated blob:
//   "[probe/<model> @ <url>] APIStatusError: Error code: 402 - {'error': {'message': '...'}} <- HTTPStatusError ..."
// Rendered verbatim that is unreadable. This collapses it to a concise
// "HTTP <status> · <message>" while leaving already-clean step details
// (e.g. "no usage block → cost recorded as zero") untouched.
export function formatProbeDetail(raw: string | null | undefined): string {
  if (!raw) return ''
  // Drop the "[probe/<model> @ <url>] " prefix and the secondary
  // "<- HTTPStatusError ..." cause chain.
  const s = raw
    .replace(/^\[probe\/[^\]]*\]\s*/, '')
    .split(' <- ')[0]
    .trim()

  const status = s.match(/error code:\s*(\d{3})/i)?.[1]
  // Upstream APIs nest the human message under a "message" key, single- or
  // double-quoted depending on whether cubepi repr'd a dict or kept raw JSON.
  const message = s.match(/["']message["']\s*:\s*["']([^"']+)["']/)?.[1]

  if (status && message) return `HTTP ${status} · ${message}`
  if (status) {
    // No extractable message: keep the leading clause, drop the "- {json..." tail.
    const head = s.split(/\s*-\s*[{[]/)[0].trim()
    return head || `HTTP ${status}`
  }
  return s
}
