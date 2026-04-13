import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  allowedDevOrigins: ['localhost', '127.0.0.1', '[::1]', '192.168.1.111'],
  compress: false,
  transpilePackages: ['katex'],
  turbopack: {},
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
