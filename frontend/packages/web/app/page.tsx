import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { AUTH_COOKIE_NAME } from '@cubebox/core'

export default async function RootRedirectPage() {
  const cookieStore = await cookies()
  const authed = !!cookieStore.get(AUTH_COOKIE_NAME)
  if (!authed) redirect('/login')

  const cookieHeader = cookieStore.toString()
  const apiUrl = process.env.CUBEBOX_API_URL ?? 'http://localhost:8000'
  const res = await fetch(`${apiUrl}/api/v1/workspaces`, {
    headers: { cookie: cookieHeader },
    cache: 'no-store',
  })
  if (!res.ok) redirect('/login')
  const workspaces = (await res.json()) as { id: string }[]
  if (workspaces.length === 0) redirect('/workspaces')
  redirect(`/w/${workspaces[0].id}`)
}
