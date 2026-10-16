import type { NextRequest } from 'next/server'

export const dynamic = 'force-dynamic'
const backendUrl = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ providerId: string }> },
) {
  const { providerId } = await params
  const headers = new Headers({ Accept: 'text/event-stream', 'Content-Type': 'application/json' })
  for (const name of ['cookie', 'x-csrf-token', 'accept-language']) {
    const value = request.headers.get(name)
    if (value) headers.set(name, value)
  }
  const response = await fetch(
    `${backendUrl}/api/v1/admin/providers/${encodeURIComponent(providerId)}/test/stream`,
    { method: 'POST', body: await request.text(), headers, signal: request.signal },
  )
  const outgoing = new Headers({
    'Content-Type': response.headers.get('content-type') ?? 'application/json',
    'Cache-Control': 'no-cache, no-transform',
    'X-Accel-Buffering': 'no',
  })
  for (const cookie of response.headers.getSetCookie()) outgoing.append('set-cookie', cookie)
  return new Response(response.body, { status: response.status, headers: outgoing })
}
