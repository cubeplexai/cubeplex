import { useTranslations } from 'next-intl'
import { AuthLanguageSwitcher } from '@/components/auth/AuthLanguageSwitcher'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'

export function AuthShell({ children }: { children: React.ReactNode }) {
  const t = useTranslations('auth')

  return (
    <main className="relative min-h-[100dvh] overflow-x-hidden bg-background text-foreground lg:h-[100dvh] lg:overflow-hidden">
      <div data-testid="auth-ambient-background" aria-hidden="true" className="absolute inset-0">
        <div className="absolute inset-0 bg-[linear-gradient(115deg,var(--color-primary)_0%,transparent_34%),linear-gradient(to_right,var(--color-background)_0%,transparent_58%)] opacity-[0.08]" />
        <div className="absolute inset-y-0 left-0 w-[68%] bg-[radial-gradient(circle_at_26%_22%,var(--color-primary)_0,transparent_28%),radial-gradient(circle_at_56%_72%,var(--color-primary)_0,transparent_34%)] opacity-[0.16]" />
        <div className="absolute inset-0 bg-[linear-gradient(to_right,var(--color-border)_1px,transparent_1px),linear-gradient(to_bottom,var(--color-border)_1px,transparent_1px)] bg-[size:48px_48px] opacity-25 [mask-image:linear-gradient(to_right,black,black_48%,transparent_78%)]" />
        <div className="absolute inset-y-0 right-0 w-[52%] bg-gradient-to-r from-transparent via-background/70 to-background" />
      </div>

      <div
        data-testid="auth-brand-logo"
        className="absolute left-5 top-5 z-20 flex items-center gap-2 md:left-8 md:top-7"
      >
        <span className="grid size-7 place-items-center rounded-md bg-primary text-[13px] font-semibold text-primary-foreground shadow-[0_12px_40px_rgba(0,112,243,0.24)]">
          c
        </span>
        <span className="text-sm font-semibold tracking-normal text-foreground">cubeplex</span>
      </div>
      <AuthLanguageSwitcher />

      <div className="relative z-10 mx-auto grid min-h-[100dvh] w-full max-w-7xl grid-cols-1 items-center gap-8 px-4 pb-6 pt-20 md:px-8 lg:h-full lg:grid-cols-[1.08fr_0.92fr] lg:gap-12 lg:px-10 lg:py-10">
        <section className="relative order-2 flex min-h-[420px] flex-col justify-center overflow-hidden lg:order-1 lg:min-h-0 lg:self-stretch lg:overflow-visible lg:pr-8">
          <div className="relative z-10 flex max-w-xl flex-col gap-5">
            <Badge variant="outline" className="w-fit border-primary/20 bg-background/70">
              {t('surfaceEyebrow')}
            </Badge>
            <div className="flex flex-col gap-4">
              <h1 className="text-4xl font-semibold leading-[1.04] tracking-normal text-foreground md:text-5xl lg:text-5xl">
                {t('surfaceTitle')}
              </h1>
              <p className="max-w-lg text-base leading-7 text-muted-foreground md:text-lg">
                {t('surfaceSubtitle')}
              </p>
            </div>
          </div>

          <div
            data-testid="cubeplex-runtime-visual"
            data-visual="animated-cube-background"
            aria-hidden="true"
            className="pointer-events-none absolute -bottom-16 -right-12 aspect-square w-[min(84vw,28rem)] opacity-80 md:-bottom-24 md:right-0 md:w-[31rem] lg:-bottom-44 lg:-right-72 lg:w-[43rem]"
          >
            <div className="absolute inset-0 rounded-full bg-primary/10 blur-3xl" />
            <div className="auth-cube-stage absolute inset-0">
              <div className="auth-cube-wire">
                <div className="auth-cube-square auth-cube-square-back" />
                <div className="auth-cube-square auth-cube-square-front" />
                <div className="auth-cube-edge auth-cube-edge-top-left" />
                <div className="auth-cube-edge auth-cube-edge-top-right" />
                <div className="auth-cube-edge auth-cube-edge-bottom-left" />
                <div className="auth-cube-edge auth-cube-edge-bottom-right" />
              </div>
            </div>
            <div className="absolute left-[18%] top-[26%] h-px w-[64%] bg-gradient-to-r from-transparent via-primary/35 to-transparent" />
            <div className="absolute left-[25%] top-[18%] h-[64%] w-px bg-gradient-to-b from-transparent via-primary/25 to-transparent" />
          </div>
        </section>

        <section className="order-1 w-full justify-self-center lg:order-2 lg:max-w-md">
          <Card className="border-border/80 bg-card shadow-sm">
            <CardContent className="px-5 py-5 md:px-6 md:py-6">{children}</CardContent>
          </Card>
        </section>
      </div>
    </main>
  )
}
