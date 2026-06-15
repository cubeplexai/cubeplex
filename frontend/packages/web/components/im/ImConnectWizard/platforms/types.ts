import type { ConnectImAccountIn } from '@cubebox/core'
import type { FC } from 'react'

export type FormState = Record<string, string>

export type FieldDef = {
  key: string
  labelKey: string
  type: 'text' | 'password' | 'select'
  required: boolean
  showIf?: (form: FormState) => boolean
  options?: { value: string; labelKey: string }[]
  placeholder?: string
}

export type PrereqItem = {
  key: string
  /** Static i18n key, or a function returning one based on form state. */
  labelKey: string | ((form: FormState) => string)
  helpUrl?: (form: FormState) => string
  /** Technical items rendered as code badges below the label. */
  items?: string[]
  /** If set, a copy button appears that writes this string to the clipboard. */
  copyJson?: string
}

export type WizardStepProps = {
  descriptor: PlatformDescriptor
  form: FormState
  onChange: (patch: Partial<FormState>) => void
  onNext: () => void
}

export type WizardStepDef = {
  key: 'prereqs' | 'credentials' | 'verify' | 'oauth_redirect' | 'manifest' | string
  labelKey: string
  Component: FC<WizardStepProps & { busy?: boolean }>
  canAdvance?: (form: FormState) => boolean
}

export type PlatformDescriptor = {
  id: 'feishu' | 'discord' | 'slack' | 'teams'
  labelKey: string
  iconName: string
  live: boolean
  prereqs: PrereqItem[]
  credentialFields: FieldDef[]
  steps: WizardStepDef[]
  buildPayload: (form: FormState) => ConnectImAccountIn
  scopeConsoleUrl: (appId: string) => string
}
