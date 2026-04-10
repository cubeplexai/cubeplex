import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  allowedDevOrigins: ['localhost', '127.0.0.1', '[::1]', '192.168.1.111'],
  compress: false,
  turbopack: {},
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.CUBEBOX_API_URL ?? 'http://localhost:8000'}/api/:path*`,
      },
    ]
  },
}

export default nextConfig
