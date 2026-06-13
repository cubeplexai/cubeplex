'use client'

import { Loader2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import type { WizardStepProps } from '../platforms/types'

type DynamicT = (key: string, values?: Record<string, string | number>) => string

export interface StepVerifyExtraProps {
  busy?: boolean
}

/**
 * Verify-step body. Renders a summary BEFORE submit and the spinner
 * only while ``busy`` (the wizard shell flips that on POST). Without
 * the gate, the user would see "connecting…" the instant they land
 * on the step — confusing because they haven't clicked Connect yet.
 */
export function StepVerify({
  descriptor,
  form,
  busy,
}: WizardStepProps & StepVerifyExtraProps): React.ReactElement {
  const t = useTranslations() as unknown as DynamicT
  if (busy) {
    return (
      <div className="flex items-center gap-3 text-sm">
        <Loader2 className="size-4 animate-spin" />
        <p>
          Verifying credentials for <code>{form.app_id}</code>…
        </p>
      </div>
    )
  }
  return (
    <div className="space-y-2 text-sm">
      <p>
        Ready to connect <strong>{t(descriptor.labelKey)}</strong> bot <code>{form.app_id}</code>.
      </p>
      <p className="text-xs text-muted-foreground">
        Press <strong>Connect</strong> to hydrate the bot identity and open the connection.
      </p>
    </div>
  )
}
