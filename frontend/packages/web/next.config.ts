// Worktree-specific overrides (ports, DB schema, Redis prefix). Loaded before
// next.config so the rewrite rule below picks up CUBEBOX_API_URL from
// .worktree.env when running inside a worktree. See
// docs/superpowers/specs/2026-04-28-worktree-parallel-dev-isolation-design.md
import dotenv from 'dotenv'
import path from 'path'
dotenv.config({
  path: path.resolve(__dirname, '../../../.worktree.env'),
  override: false,
})

import type { NextConfig } from 'next'
import createNextIntlPlugin from 'next-intl/plugin'

const withNextIntl = createNextIntlPlugin('./i18n/request.ts')

// CSP for /admin/* routes. The security-critical directive here is
// `frame-src 'self'` — it prevents the plugin-manifest iframe from being
// redirected to an arbitrary URL. `default-src` includes 'unsafe-inline'
// and 'unsafe-eval' to accommodate Next.js dev mode (React Refresh inlines
// scripts; HMR uses eval). Production CSP tightening (nonce-based
// script-src, dropping unsafe-eval) is tracked in M12 per backlog.
const ADMIN_CSP = "frame-src 'self'; default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:"

const nextConfig: NextConfig = {
  allowedDevOrigins: ['localhost', '127.0.0.1', '[::1]', '192.168.1.111'],
  compress: false,
  transpilePackages: ['katex', '@cubebox/core'],
  turbopack: {},
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
