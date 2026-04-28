import { getRequestConfig } from 'next-intl/server'
import { cookies, headers } from 'next/headers'

const SUPPORTED_LOCALES = ['en', 'zh'] as const
type Locale = (typeof SUPPORTED_LOCALES)[number]

function detectLocale(cookieValue: string | undefined, acceptLanguage: string | null): Locale {
  if (cookieValue && (SUPPORTED_LOCALES as readonly string[]).includes(cookieValue)) {
    return cookieValue as Locale
  }
  if (acceptLanguage?.split(',')[0]?.toLowerCase().startsWith('zh')) return 'zh'
  return 'en'
}

export default getRequestConfig(async () => {
  const cookieStore = await cookies()
  const headerList = await headers()
  const locale = detectLocale(
    cookieStore.get('NEXT_LOCALE')?.value,
    headerList.get('accept-language'),
  )
  const messages = (await import(`../messages/${locale}.json`)).default
  return { locale, messages }
})
