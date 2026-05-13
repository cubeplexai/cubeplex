import { getTranslations } from 'next-intl/server'

export default async function AuthLayout({ children }: { children: React.ReactNode }) {
  const t = await getTranslations('authLayout')
  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Top brand strip — restrained, no marketing language */}
      <header className="px-8 py-5 flex items-center gap-2">
        <div
          className="w-[22px] h-[22px] rounded-[5px] bg-foreground text-background grid place-items-center font-mono font-semibold text-[11px] leading-none"
          aria-hidden
        >
          cb
        </div>
        <span className="font-display text-[14px] font-semibold tracking-tight text-foreground">
          cubebox
        </span>
      </header>

      <main className="flex-1 flex items-center justify-center px-6 pb-16">
        <div className="w-full max-w-[380px]">{children}</div>
      </main>

      <footer className="px-8 py-4 hairline-t flex items-center justify-between text-[11px] text-muted-foreground font-mono">
        <span>{t('footerBrand')}</span>
        <span>{t('footerSecurity')}</span>
      </footer>
    </div>
  )
}
