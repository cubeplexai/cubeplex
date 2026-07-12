import type { VendorPreset } from '@cubeplex/core'

export type WizardStep = 1 | 2 | 3 | 4

export interface CreatedModel {
  /** Database id (mdl_…) */
  id: string
  /** Vendor model id */
  model_id: string
  /** Human-friendly label */
  display_name: string
}

/** The Configure-step form fields. Persisted in wizard state so stepping away
 *  and back (which unmounts ConfigureStep) doesn't reset them to preset defaults. */
export interface ConfigFormValues {
  name: string
  slug: string
  slugTouched: boolean
  baseUrl: string
  apiKey: string
  authChoice: 'api_key' | 'none'
  capability: Record<string, unknown>
  logoUrl: string
  extraHeaders: string
}

/** Tagged with the endpoint it was entered under, so switching endpoints
 *  (a different preset_key) correctly falls back to that endpoint's defaults. */
export interface ConfigDraft extends ConfigFormValues {
  presetKey: string
}

export interface WizardState {
  step: WizardStep
  vendor: VendorPreset | null
  /** Endpoint chosen in step 2 (region/protocol/plan) — its preset_key. */
  selectedPresetKey: string | null
  providerId: string | null
  models: CreatedModel[]
  /** Last-entered Configure-step values, so a revisit restores them. */
  configDraft: ConfigDraft | null
}

export type WizardAction =
  | { type: 'pickVendor'; vendor: VendorPreset }
  | { type: 'selectEndpoint'; presetKey: string }
  | { type: 'providerCreated'; providerId: string }
  | { type: 'modelsCreated'; models: CreatedModel[] }
  | { type: 'setConfigDraft'; draft: ConfigDraft }
  | { type: 'next' }
  | { type: 'back' }

export const initialWizardState: WizardState = {
  step: 1,
  vendor: null,
  selectedPresetKey: null,
  providerId: null,
  models: [],
  configDraft: null,
}

// Whether `next` may advance from the given state. Step 1 needs a vendor (the
// endpoint is chosen in step 2); step 2 needs a persisted provider; step 3 needs
// at least one model.
export function canAdvance(state: WizardState): boolean {
  switch (state.step) {
    case 1:
      return state.vendor !== null
    case 2:
      return state.providerId !== null
    case 3:
      return state.models.length > 0
    case 4:
      return false
  }
}

export function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case 'pickVendor':
      // Picking a (new) vendor resets the downstream endpoint choice.
      return { ...state, vendor: action.vendor, selectedPresetKey: null }
    case 'selectEndpoint':
      return { ...state, selectedPresetKey: action.presetKey }
    case 'providerCreated':
      return { ...state, providerId: action.providerId }
    case 'modelsCreated':
      return { ...state, models: action.models }
    case 'setConfigDraft':
      return { ...state, configDraft: action.draft }
    case 'next': {
      if (!canAdvance(state)) return state
      return { ...state, step: (state.step + 1) as WizardStep }
    }
    case 'back': {
      if (state.step === 1) return state
      return { ...state, step: (state.step - 1) as WizardStep }
    }
  }
}
