import { NextResponse, type NextRequest } from 'next/server'

const PUBLIC_PATHS = ['/login', '/register']
const PROTECTED_PREFIXES = ['/w/', '/workspaces']

function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some(
    (p) => pathname === p.replace(/\/$/, '') || pathname.startsWith(p)
  )
}

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl
  const hasAuth = !!request.cookies.get('cubebox_auth')

  if (!hasAuth && isProtected(pathname)) {
    const url = request.nextUrl.clone()
    url.pathname = '/login'
    url.searchParams.set('next', pathname + request.nextUrl.search)
    return NextResponse.redirect(url)
  }
  if (hasAuth && PUBLIC_PATHS.includes(pathname)) {
    const url = request.nextUrl.clone()
    url.pathname = '/'
    url.search = ''
    return NextResponse.redirect(url)
  }
  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|icon.svg|favicon.ico).*)'],
}
