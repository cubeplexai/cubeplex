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

  const [rawPath, ...hashParts] = href.split('#')
  const hash = hashParts.length > 0 ? '#' + hashParts.join('#') : null

  const baseDir = filePath.includes('/') ? filePath.slice(0, filePath.lastIndexOf('/')) : ''
  const joined = rawPath.startsWith('/') ? `${SANDBOX_ROOT}${rawPath}` : `${baseDir}/${rawPath}`
  const path = normalizePath(joined)
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
