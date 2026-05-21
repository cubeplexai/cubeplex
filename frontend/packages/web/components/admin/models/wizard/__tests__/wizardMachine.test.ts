import { describe, expect, it } from 'vitest'
import { canAdvance, initialWizardState, wizardReducer, type WizardState } from '../wizardMachine'
import { makePreset } from './fixtures'

describe('wizardMachine', () => {
  it('starts at step 1 with empty state', () => {
    expect(initialWizardState).toEqual({
      step: 1,
      preset: null,
      providerId: null,
      modelDbIds: [],
    })
  })

  it('pickPreset stores the preset', () => {
    const preset = makePreset()
    const s = wizardReducer(initialWizardState, { type: 'pickPreset', preset })
    expect(s.preset).toBe(preset)
    expect(s.step).toBe(1)
  })

  it('providerCreated stores the provider id', () => {
    const s = wizardReducer(initialWizardState, { type: 'providerCreated', providerId: 'prv_1' })
    expect(s.providerId).toBe('prv_1')
  })

  it('modelsCreated stores the model db ids', () => {
    const s = wizardReducer(initialWizardState, {
      type: 'modelsCreated',
      modelDbIds: ['mdl_1', 'mdl_2'],
    })
    expect(s.modelDbIds).toEqual(['mdl_1', 'mdl_2'])
  })

  it('next does not advance from step 1 without a preset', () => {
    const s = wizardReducer(initialWizardState, { type: 'next' })
    expect(s.step).toBe(1)
  })

  it('next advances to step 2 once a preset is picked', () => {
    let s = wizardReducer(initialWizardState, { type: 'pickPreset', preset: makePreset() })
    s = wizardReducer(s, { type: 'next' })
    expect(s.step).toBe(2)
  })

  it('cannot advance to step 3 unless providerId is set', () => {
    const atStep2: WizardState = { ...initialWizardState, step: 2, preset: makePreset() }
    expect(canAdvance(atStep2)).toBe(false)
    expect(wizardReducer(atStep2, { type: 'next' }).step).toBe(2)

    const withProvider = wizardReducer(atStep2, { type: 'providerCreated', providerId: 'prv_1' })
    expect(canAdvance(withProvider)).toBe(true)
    expect(wizardReducer(withProvider, { type: 'next' }).step).toBe(3)
  })

  it('cannot advance to step 4 unless at least one model exists', () => {
    const atStep3: WizardState = {
      ...initialWizardState,
      step: 3,
      preset: makePreset(),
      providerId: 'prv_1',
    }
    expect(canAdvance(atStep3)).toBe(false)
    expect(wizardReducer(atStep3, { type: 'next' }).step).toBe(3)

    const withModels = wizardReducer(atStep3, { type: 'modelsCreated', modelDbIds: ['mdl_1'] })
    expect(canAdvance(withModels)).toBe(true)
    expect(wizardReducer(withModels, { type: 'next' }).step).toBe(4)
  })

  it('back steps down but never below step 1', () => {
    const atStep2: WizardState = { ...initialWizardState, step: 2 }
    expect(wizardReducer(atStep2, { type: 'back' }).step).toBe(1)
    expect(wizardReducer(initialWizardState, { type: 'back' }).step).toBe(1)
  })

  it('step 4 cannot advance further', () => {
    const atStep4: WizardState = { ...initialWizardState, step: 4 }
    expect(canAdvance(atStep4)).toBe(false)
  })
})
