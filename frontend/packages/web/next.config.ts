import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  allowedDevOrigins: ['localhost', '127.0.0.1', '[::1]', '192.168.1.111'],
  compress: false,
  turbopack: {},
  experimental: {
    // Default is 30s which kills long-running SSE streams through rewrites proxy.
    // Runtime: `proxyTimeout === null ? undefined : proxyTimeout || 30000`
    // null disables timeout, but type only allows number | undefined.
    // @ts-expect-error -- null is intentional; see next/dist/.../proxy-request.js
    proxyTimeout: null,
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
