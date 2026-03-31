import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  allowedDevOrigins: ['192.168.1.111'],
  compress: false,
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
