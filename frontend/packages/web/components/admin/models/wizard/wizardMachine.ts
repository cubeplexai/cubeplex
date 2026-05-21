import type { ProviderPreset } from '@cubebox/core'

export type WizardStep = 1 | 2 | 3 | 4

export interface WizardState {
  step: WizardStep
  preset: ProviderPreset | null
  providerId: string | null
  modelDbIds: string[]
}

export type WizardAction =
  | { type: 'pickPreset'; preset: ProviderPreset }
  | { type: 'providerCreated'; providerId: string }
  | { type: 'modelsCreated'; modelDbIds: string[] }
  | { type: 'next' }
  | { type: 'back' }

export const initialWizardState: WizardState = {
  step: 1,
  preset: null,
  providerId: null,
  modelDbIds: [],
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
      return state.modelDbIds.length > 0
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
      return { ...state, modelDbIds: action.modelDbIds }
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
