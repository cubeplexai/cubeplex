import type { NextConfig } from 'next'
import createNextIntlPlugin from 'next-intl/plugin'

const withNextIntl = createNextIntlPlugin('./i18n/request.ts')

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
