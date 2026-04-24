import type { NextConfig } from 'next'

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

export default nextConfig
