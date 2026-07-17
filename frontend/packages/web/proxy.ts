import { NextResponse, type NextRequest } from 'next/server'
import { AUTH_COOKIE_NAME } from '@cubeplex/core'

const PUBLIC_PATHS = ['/login', '/register']
const PROTECTED_PREFIXES = ['/w/', '/workspaces', '/admin', '/onboarding']

function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((p) => pathname === p.replace(/\/$/, '') || pathname.startsWith(p))
}

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl
  const hasAuth = !!request.cookies.get(AUTH_COOKIE_NAME)

  if (!hasAuth && isProtected(pathname)) {
    const url = request.nextUrl.clone()
    url.pathname = '/login'
    url.searchParams.set('next', pathname + request.nextUrl.search)
    return NextResponse.redirect(url)
  }
  if (hasAuth && PUBLIC_PATHS.includes(pathname)) {
    // A ?next= param means an upstream server-side guard (page.tsx) or the
    // client-side onUnauthorized listener has already determined the cookie
    // is stale and explicitly routed the user here. Without this escape
    // hatch, a stale cookie + /login redirect creates an infinite
    // /login → / → 401 → /login loop because the proxy can't validate the
    // cookie against the backend.
    const hasNext = request.nextUrl.searchParams.has('next')
    if (hasNext) return NextResponse.next()
    const url = request.nextUrl.clone()
    url.pathname = '/'
    url.search = ''
    return NextResponse.redirect(url)
  }
  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|icon.png).*)'],
}
