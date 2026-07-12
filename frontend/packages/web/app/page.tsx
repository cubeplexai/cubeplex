import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { AUTH_COOKIE_NAME } from '@cubeplex/core'

export default async function RootRedirectPage() {
  const cookieStore = await cookies()
  const authed = !!cookieStore.get(AUTH_COOKIE_NAME)
  if (!authed) redirect('/login')

  const cookieHeader = cookieStore.toString()
  const apiUrl = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'
  const res = await fetch(`${apiUrl}/api/v1/workspaces`, {
    headers: { cookie: cookieHeader },
    cache: 'no-store',
  })
  // 401 = stale cookie (user deleted, DB reset, etc.). Bounce to /login with
  // a ?next= so proxy.ts knows not to redirect back to / (see proxy.ts for
  // the cookie-vs-?next disambiguation).
  if (!res.ok) redirect('/login?next=/')
  const workspaces = (await res.json()) as { id: string }[]
  if (workspaces.length === 0) redirect('/workspaces')
  redirect(`/w/${workspaces[0].id}`)
}
