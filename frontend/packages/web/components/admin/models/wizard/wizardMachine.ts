import type { ProviderPreset } from '@cubebox/core'

export type WizardStep = 1 | 2 | 3 | 4

export interface CreatedModel {
  /** Database id (mdl_…) */
  id: string
  /** Vendor model id */
  model_id: string
  /** Human-friendly label */
  display_name: string
}

export interface WizardState {
  step: WizardStep
  preset: ProviderPreset | null
  providerId: string | null
  models: CreatedModel[]
}

export type WizardAction =
  | { type: 'pickPreset'; preset: ProviderPreset }
  | { type: 'providerCreated'; providerId: string }
  | { type: 'modelsCreated'; models: CreatedModel[] }
  | { type: 'next' }
  | { type: 'back' }

export const initialWizardState: WizardState = {
  step: 1,
  preset: null,
  providerId: null,
  models: [],
}

// Whether `next` may advance from the given state. Step 1 needs a preset,
// step 2 needs a persisted provider, step 3 needs at least one model.
export function canAdvance(state: WizardState): boolean {
  switch (state.step) {
    case 1:
      return state.preset !== null
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
    case 'pickPreset':
      return { ...state, preset: action.preset }
    case 'providerCreated':
      return { ...state, providerId: action.providerId }
    case 'modelsCreated':
      return { ...state, models: action.models }
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
