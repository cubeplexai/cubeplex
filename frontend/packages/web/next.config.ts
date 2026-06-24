// Worktree-specific overrides (ports, DB schema, Redis prefix). Loaded before
// next.config so the rewrite rule below picks up CUBEBOX_API_URL from
// .worktree.env when running inside a worktree. See
// docs/dev/specs/2026-04-28-worktree-parallel-dev-isolation-design.md
import dotenv from 'dotenv'
import path from 'path'
dotenv.config({
  path: path.resolve(__dirname, '../../../.worktree.env'),
  override: false,
})

import type { NextConfig } from 'next'
import createNextIntlPlugin from 'next-intl/plugin'

const withNextIntl = createNextIntlPlugin('./i18n/request.ts')
export const ATTACHMENT_PROXY_BODY_LIMIT = '60mb'

// CSP for /admin/* routes. The security-critical directive here is
// `frame-src 'self'` — it prevents the plugin-manifest iframe from being
// redirected to an arbitrary URL. `default-src` includes 'unsafe-inline'
// and 'unsafe-eval' to accommodate Next.js dev mode (React Refresh inlines
// scripts; HMR uses eval). Production CSP tightening (nonce-based
// script-src, dropping unsafe-eval) is tracked in M12 per backlog.
const ADMIN_CSP = "frame-src 'self'; default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:"

export const nextConfig: NextConfig = {
  allowedDevOrigins: [
    'localhost',
    '127.0.0.1',
    '[::1]',
    '192.168.1.111',
    '192.168.1.150',
    '192.168.1.215',
  ],
  compress: false,
  // Produce a self-contained Next.js bundle (node_modules + server.js) for
  // container deploys. Gated on an env var so dev / E2E aren't affected —
  // the Dockerfile sets NEXT_OUTPUT=standalone before `next build`.
  output: process.env.NEXT_OUTPUT === 'standalone' ? 'standalone' : undefined,
  transpilePackages: ['katex', '@cubebox/core'],
  // Pin the workspace root to the frontend monorepo. Otherwise Next walks up,
  // finds the user's global ~/pnpm-lock.yaml (for global CLI tools), and picks
  // /home/chris as the root — which misroots module resolution (e.g. resolving
  // `tailwindcss` from frontend/packages instead of packages/web, so it 404s).
  turbopack: { root: path.resolve(__dirname, '../..') },
  // Inline cookie-name overrides into the client bundle. Worktrees set these
  // in .worktree.env so each worktree's browser cookies don't collide on
  // localhost (cookies are host-scoped, not port-scoped).
  env: {
    NEXT_PUBLIC_AUTH_COOKIE_NAME: process.env.NEXT_PUBLIC_AUTH_COOKIE_NAME ?? 'cubebox_auth',
    NEXT_PUBLIC_CSRF_COOKIE_NAME: process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME ?? 'cubebox_csrf',
  },
  experimental: {
    proxyClientMaxBodySize: ATTACHMENT_PROXY_BODY_LIMIT,
  },
  async headers() {
    return [
      {
        source: '/admin/:path*',
        headers: [{ key: 'Content-Security-Policy', value: ADMIN_CSP }],
      },
    ]
  },
  async rewrites() {
    return {
      beforeFiles: [],
      afterFiles: [],
      // fallback: checked AFTER all filesystem routes (including dynamic route
      // handlers like app/api/v1/conversations/[id]/messages/route.ts).
      // This ensures our SSE streaming route handler takes precedence.
      fallback: [
        {
          source: '/api/:path*',
          destination: `${process.env.CUBEBOX_API_URL ?? 'http://localhost:8000'}/api/:path*`,
        },
      ],
    }
  },
}

export default withNextIntl(nextConfig)
