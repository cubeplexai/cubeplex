/**
 * Conversations messages endpoint — Route Handler proxy.
 *
 * POST: SSE streaming proxy that bypasses Next.js rewrite proxy (which uses
 *   http-proxy with 30s default timeout and response buffering). Pipes the
 *   backend SSE stream through the Web Streams API — no timeout, no buffering.
 *
 * GET: Plain JSON proxy for message history (list messages).
 */
import { type NextRequest, NextResponse } from 'next/server'

const BACKEND_URL = process.env.CUBEBOX_API_URL ?? 'http://localhost:8000'

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  const body = await request.text()

  const backendRes = await fetch(
    `${BACKEND_URL}/api/v1/conversations/${id}/messages`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
      },
      body,
    },
  )

  if (!backendRes.ok || !backendRes.body) {
    return new Response(await backendRes.text(), {
      status: backendRes.status,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Pipe the backend SSE stream straight through — no buffering
  return new Response(backendRes.body, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      'Connection': 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  })
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  const url = new URL(request.url)
  const qs = url.search

  const backendRes = await fetch(
    `${BACKEND_URL}/api/v1/conversations/${id}/messages${qs}`,
  )

  const data = await backendRes.json()
  return NextResponse.json(data, { status: backendRes.status })
}
