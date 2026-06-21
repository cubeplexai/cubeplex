export const SANDBOX_ROOT = '/workspace'

export type SandboxHref =
  | { kind: 'sandbox'; path: string; hash: string | null }
  | { kind: 'external'; href: string }
  | { kind: 'anchor'; hash: string }

/**
 * Resolve a markdown `href` relative to the markdown file's sandbox path.
 *
 * - `http(s)://…`, `mailto:`, `tel:`, data/blob URIs → external (open in new tab)
 * - `#section` → in-page anchor
 * - `/foo/bar.md` → absolute under SANDBOX_ROOT (so users can write workspace-root paths)
 * - everything else → relative to the markdown file's directory
 */
export function resolveSandboxHref(filePath: string, href: string): SandboxHref {
  if (!href) return { kind: 'external', href }
  if (href.startsWith('#')) return { kind: 'anchor', hash: href }
  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return { kind: 'external', href }
  if (href.startsWith('//')) return { kind: 'external', href }

  const [beforeHash, ...hashParts] = href.split('#')
  const hash = hashParts.length > 0 ? '#' + hashParts.join('#') : null
  // Drop query string — sandbox file lookup doesn't accept ?key=value, and
  // markdown usually embeds query only for cache-busting on assets.
  const rawPath = beforeHash.split('?', 1)[0]
  // Decode the whole path BEFORE splitting on `/` so encoded slashes
  // (`%2f`) and encoded dots (`%2e`) can't smuggle traversal inside a
  // single raw segment. Falls back to the literal string on a malformed
  // escape; backend remains the security boundary either way.
  const decoded = safeDecode(rawPath)

  const baseDir = filePath.includes('/') ? filePath.slice(0, filePath.lastIndexOf('/')) : ''
  const joined = decoded.startsWith('/') ? `${SANDBOX_ROOT}${decoded}` : `${baseDir}/${decoded}`
  const normalized = normalizePath(joined)
  // Clamp to sandbox root: a malformed href like `../../../../etc/passwd` should
  // not produce a path the rest of the UI tries to fetch from outside /workspace.
  const path =
    normalized === SANDBOX_ROOT || normalized.startsWith(`${SANDBOX_ROOT}/`)
      ? normalized
      : SANDBOX_ROOT
  return { kind: 'sandbox', path, hash }
}

function normalizePath(p: string): string {
  const stack: string[] = []
  for (const seg of p.split('/')) {
    if (seg === '' || seg === '.') continue
    if (seg === '..') {
      if (stack.length > 0) stack.pop()
      continue
    }
    stack.push(seg)
  }
  return '/' + stack.join('/')
}

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s)
  } catch {
    return s
  }
}
